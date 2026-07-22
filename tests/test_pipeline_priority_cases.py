from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline.config import (
    ImageGenerationConfig,
    RetryStrategyConfig,
    TemperaturePolicyConfig,
    load_config,
)
from pipeline.consistency import ConsistencyCheckError, PipelineConsistencyChecker
from pipeline.engine import AttemptPlan, ExecutionOptions, StepExecutionEngine
from pipeline.prompt_impact import PromptImpactReporter
from pipeline.prompts import PromptCatalog
from pipeline.scenario_review import (
    ReviewOutlineStep,
    ReviewSectionsStep,
    _inline_common_refs,
)
from pipeline.steps import (
    GeneratePlanningInputStep,
    GenerateCharacterProfilesStep,
    GenerateOutlineStep,
    GenerateSectionsStep,
    build_minimal_steps,
)
from pipeline.state import StepState
from pipeline.text_generation import GenerationResponse, MockTextGenerationProvider
from pipeline.types import Step, StepContext, StepResult


class ConstantOutputStep(Step):
    def __init__(self, name: str, output: dict[str, object]) -> None:
        self.name = name
        self.output = output
        self.calls = 0

    def run(self, context: StepContext) -> StepResult:
        self.calls += 1
        return StepResult(
            output=self.output,
            prompt=f"prompt:{self.name}",
            model="test-model",
            temperature=context.config.temperature_for(self.name),
            input_tokens=10,
            output_tokens=20,
        )


class FlakyStep(Step):
    def __init__(self, name: str, fail_times: int, output: dict[str, object]) -> None:
        self.name = name
        self.fail_times = fail_times
        self.output = output
        self.calls = 0

    def run(self, context: StepContext) -> StepResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"planned-fail-{self.calls}")
        return StepResult(output=self.output)


class StrategyAwareStep(Step):
    def __init__(self) -> None:
        self.phases: list[str] = []
        self.failure_reasons: list[str] = []

    name = "strategy-aware"

    def run(self, context: StepContext) -> StepResult:
        phase = "initial" if not self.phases else "short_retry"
        self.phases.append(phase)
        raise RuntimeError(f"{phase}-failed")

    def run_with_prompt_revision(
        self, context: StepContext, failure_reason: str
    ) -> StepResult:
        self.phases.append("prompt_revision")
        self.failure_reasons.append(failure_reason)
        raise RuntimeError("prompt-revision-failed")

    def run_fallback(self, context: StepContext, failure_reason: str) -> StepResult:
        self.phases.append("fallback")
        self.failure_reasons.append(failure_reason)
        return StepResult(output={"fallback": True})


class ContradictingProfileStep(GenerateCharacterProfilesStep):
    def __init__(self) -> None:
        self.calls = 0

    def run(self, context: StepContext) -> StepResult:
        self.calls += 1
        result = super().run(context)
        result.output["character_profiles"][0]["name"] = "inconsistent-name"
        return result


class TemperatureBypassStep(Step):
    name = "temperature-bypass"

    def run(self, context: StepContext) -> StepResult:
        return StepResult(output={"ok": True}, temperature=1.5)


def test_p0_skip_completed_step_and_load_artifact(make_context) -> None:
    """P0: completed artifact should be loaded and step execution skipped."""
    context, trace = make_context()
    step = ConstantOutputStep("step-a", {"a": 1})
    engine = StepExecutionEngine([step])

    artifact_path = Path(context.artifacts_dir) / "step-a.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps({"a": 999}), encoding="utf-8")
    context.state_store.upsert_step(
        StepState(
            name="step-a",
            status="completed",
            attempts=1,
            artifact_path=str(artifact_path),
        )
    )

    out = engine.run(context)

    assert step.calls == 0
    assert out["a"] == 999
    assert any(e.get("event") == "step_skipped" for e in trace.events)


def test_p0_from_step_preloads_dependencies(make_context) -> None:
    """P0: from-step restart should preload prerequisite artifacts."""
    context, trace = make_context(include_input=False)

    step1 = ConstantOutputStep("step-1", {"k1": "v1"})
    step2 = ConstantOutputStep("step-2", {"k2": "v2"})
    step3 = ConstantOutputStep("step-3", {"k3": "v3"})
    engine = StepExecutionEngine([step1, step2, step3])

    for name, payload in (("step-1", {"k1": "pre1"}), ("step-2", {"k2": "pre2"})):
        artifact = Path(context.artifacts_dir) / f"{name}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(payload), encoding="utf-8")
        context.state_store.upsert_step(
            StepState(name=name, status="completed", attempts=1, artifact_path=str(artifact))
        )

    out = engine.run(context, options=ExecutionOptions(from_step="step-3"))

    assert step1.calls == 0
    assert step2.calls == 0
    assert step3.calls == 1
    assert out["k1"] == "pre1"
    assert out["k2"] == "pre2"
    assert out["k3"] == "v3"
    assert sum(1 for e in trace.events if e.get("event") == "step_preloaded") == 2


def test_p0_from_step_fails_when_prerequisite_artifact_missing(make_context) -> None:
    """P0: restart from middle must fail if prerequisite artifact is missing."""
    context, _ = make_context(include_input=False)
    step1 = ConstantOutputStep("step-1", {"k1": "v1"})
    step2 = ConstantOutputStep("step-2", {"k2": "v2"})
    engine = StepExecutionEngine([step1, step2])

    with pytest.raises(RuntimeError, match="prerequisite artifact is missing"):
        engine.run(context, options=ExecutionOptions(from_step="step-2"))


def test_p0_retry_and_failure_state_tracking(make_context, base_config) -> None:
    """P0: failed step should be retried up to max_retries+1 and then complete when succeeding."""
    context, trace = make_context()
    flaky = FlakyStep("step-flaky", fail_times=1, output={"ok": True})
    engine = StepExecutionEngine([flaky])

    out = engine.run(context)

    assert flaky.calls == 2
    assert out["ok"] is True
    state = context.state_store.get_step("step-flaky")
    assert state is not None
    assert state.status == "completed"
    assert state.attempts == 2
    assert any(e.get("event") == "step_failed" for e in trace.events)
    assert any(e.get("event") == "step_succeeded" for e in trace.events)


def test_retry_strategy_runs_distinct_phases(make_context) -> None:
    context, trace = make_context()
    context.config.retry_strategy = RetryStrategyConfig(
        short_retries=1,
        prompt_revision_retries=1,
        fallback_enabled=True,
    )
    step = StrategyAwareStep()

    output = StepExecutionEngine([step]).run(context)

    assert output["fallback"] is True
    assert step.phases == ["initial", "short_retry", "prompt_revision", "fallback"]
    assert step.failure_reasons == ["short_retry-failed", "prompt-revision-failed"]
    scheduled_phases = [
        event["retry_phase"]
        for event in trace.events
        if event.get("event") == "step_retry_scheduled"
    ]
    assert scheduled_phases == ["short_retry", "prompt_revision", "fallback"]
    state = context.state_store.get_step(step.name)
    assert state is not None
    assert state.retry_phase == "fallback"


def test_p1_force_true_reexecutes_completed_step(make_context) -> None:
    """P1: force option should bypass skip path and execute step again."""
    context, _ = make_context()
    step = ConstantOutputStep("step-force", {"v": 2})
    engine = StepExecutionEngine([step])

    artifact = Path(context.artifacts_dir) / "step-force.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"v": 1}), encoding="utf-8")
    context.state_store.upsert_step(
        StepState(name="step-force", status="completed", attempts=1, artifact_path=str(artifact))
    )

    out = engine.run(context, options=ExecutionOptions(force=True))

    assert step.calls == 1
    assert out["v"] == 2


def test_p1_unknown_from_step_should_error(make_context) -> None:
    """P1: unknown from-step should fail fast with explicit error."""
    context, _ = make_context()
    step = ConstantOutputStep("known-step", {"x": 1})
    engine = StepExecutionEngine([step])

    with pytest.raises(RuntimeError, match="Unknown from-step"):
        engine.run(context, options=ExecutionOptions(from_step="unknown-step"))


def test_p1_config_default_and_partial_override(tmp_path: Path) -> None:
    """P1: config loader should keep defaults and apply nested partial overrides."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "text_generation": {
                    "model": "overridden",
                    "timeout_seconds": 30,
                    "api_key_env": "TEST_TEXT_API_KEY",
                },
                "image_generation": {"provider": "stub"},
            }
        ),
        encoding="utf-8",
    )

    conf = load_config(str(cfg_path))

    assert conf.model_name == "overridden"
    assert conf.text_generation.provider == "mock"
    assert conf.text_generation.timeout_seconds == 30
    assert conf.text_generation.api_key_env == "TEST_TEXT_API_KEY"
    assert conf.retry_strategy == RetryStrategyConfig()
    assert conf.temperature_policy == TemperaturePolicyConfig()
    assert conf.image_generation.provider == "stub"
    assert conf.image_generation.model == ImageGenerationConfig().model
    assert conf.image_generation.expression_sheet_width == 2048
    assert conf.image_generation.expression_sheet_height == 2048
    assert conf.image_generation.quality == "high"
    assert conf.image_generation.output_format == "png"
    assert conf.image_generation.timeout_seconds == 120
    assert conf.image_generation.api_key_env == "OPENAI_API_KEY"
    assert conf.scenario_body_generation.subsections_per_section == 3
    assert conf.scenario_body_generation.target_characters == 1200
    assert conf.scenario_body_generation.min_characters == 850
    assert conf.scenario_body_generation.max_characters == 1600
    assert conf.character_profile_generation.enabled is False
    assert conf.character_profile_generation.require_review is True
    assert conf.planning_input_generation.enabled is False
    assert conf.planning_input_generation.require_review is True
    assert conf.scenario_review.enabled is False
    assert conf.scenario_review.require_human_review is False


def test_scenario_review_can_be_enabled(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "scenario_review": {
                    "enabled": True,
                    "require_human_review": True,
                }
            }
        ),
        encoding="utf-8",
    )

    conf = load_config(str(cfg_path))

    assert conf.scenario_review.enabled is True
    assert conf.scenario_review.require_human_review is True


def test_openai_example_uses_manageable_outline_subsection_count() -> None:
    config_path = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "pipeline.openai.config.json"
    )

    conf = load_config(str(config_path))

    assert conf.scenario_body_generation.subsections_per_section == 3


def test_review_steps_are_inserted_around_generation() -> None:
    names = [
        step.name for step in build_minimal_steps(include_scenario_review=True)
    ]

    assert names.index("step-02-review-outline") == names.index(
        "step-02-generate-outline"
    ) + 1
    assert names.index("step-04-review-sections") == names.index(
        "step-04-generate-sections"
    ) + 1


def test_outline_review_api_schema_has_no_unresolved_refs() -> None:
    schemas_dir = Path(__file__).resolve().parent.parent / "schemas"
    outline_schema = json.loads(
        (schemas_dir / "scenario-outline.schema.json").read_text(encoding="utf-8")
    )

    api_schema = _inline_common_refs(outline_schema, schemas_dir)

    assert '"$ref"' not in json.dumps(api_schema)
    story_event = api_schema["properties"]["chapters"]["items"]["properties"][
        "sections"
    ]["items"]["properties"]["key_events"]["items"]
    assert story_event["required"] == ["event_id", "description"]

    def assert_strict_objects(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                assert_strict_objects(item)
            return
        if not isinstance(value, dict):
            return
        properties = value.get("properties")
        if isinstance(properties, dict):
            assert value.get("required") == list(properties)
            assert value.get("additionalProperties") is False
        for item in value.values():
            assert_strict_objects(item)

    assert_strict_objects(api_schema)


def test_outline_review_restores_immutable_title_and_logline() -> None:
    reviewed = ReviewOutlineStep._restore_immutable_fields(
        {
            "title": "Paraphrased title",
            "logline": "Paraphrased premise",
            "story_plan": {},
            "chapters": [],
        },
        {"title": "Canonical title", "premise": "Canonical premise"},
    )

    assert reviewed["title"] == "Canonical title"
    assert reviewed["logline"] == "Canonical premise"


def test_outline_review_prompt_requires_narratively_earned_introductions() -> None:
    prompt = ReviewOutlineStep._chapter_prompt(
        shared_input={"scenario_idea": {}},
        profiles=[],
        story_plan={},
        previous_chapter=None,
        draft_chapter={"chapter_no": 1, "sections": []},
    )

    assert "introductions must feel narratively earned" in prompt
    assert "first direct conversation" in prompt
    assert "reason the character is present" in prompt
    assert "premature participation" in prompt
    assert "does not make the character a scene participant" in prompt
    assert "participant_presence" in prompt
    assert "location_status_before" in prompt


def test_outline_review_restores_chapter_section_and_event_identity() -> None:
    event = {"event_id": "phase-1-beat-1", "description": "Draft event"}
    planned_updates = {
        "character_locations": [],
        "possessions": [],
        "known_information": [],
        "relationship_changes": [],
        "introduced_entities": [],
        "opened_thread_ids": [],
        "resolved_thread_ids": [],
        "planted_clue_ids": [],
        "paid_off_clue_ids": [],
        "character_arc_changes": [],
        "resulting_state_summary": "The event is complete.",
    }
    subsection = {
        "subsection_no": 1,
        "subsection_title": "Beat",
        "subsection_purpose": "Advance",
        "key_events": [deepcopy(event)],
        "start_state": "Before",
        "state_change": deepcopy(event),
        "end_state": "After",
        "planned_state_updates": planned_updates,
        "must_not_repeat": ["Do not repeat."],
    }
    draft = {
        "chapter_no": 5,
        "chapter_title": "Draft",
        "chapter_goal": "Goal",
        "sections": [
            {
                "section_no": 6,
                "section_title": "Section",
                "section_purpose": "Purpose",
                "scene_location": "office",
                "scene_activity": "The protagonist reviews the case.",
                "scene_phase": "setup",
                "key_events": [deepcopy(event)],
                "participating_characters": ["c001"],
                "participant_presence": [
                    {
                        "character_id": "c001",
                        "presence_mode": "in_person",
                        "first_appearance": True,
                        "location_status_before": "not_introduced",
                        "location_before": None,
                        "entry_explanation": "The protagonist begins in the office.",
                        "scene_role": "investigator",
                        "current_activity": "Reviewing the case file.",
                        "participation_status": "active",
                    }
                ],
                "subsections": [deepcopy(subsection)],
            }
        ],
    }
    reviewed = deepcopy(draft)
    reviewed["chapter_no"] = 1
    reviewed["sections"][0]["section_no"] = 1
    reviewed["sections"][0]["key_events"][0]["event_id"] = "wrong"
    reviewed["sections"][0]["subsections"][0]["subsection_no"] = 1
    reviewed["sections"][0]["subsections"][0]["key_events"][0][
        "event_id"
    ] = "wrong"
    reviewed["sections"][0]["subsections"][0]["state_change"][
        "event_id"
    ] = "wrong"

    restored = ReviewOutlineStep._restore_chapter_identity(reviewed, draft)

    assert restored["chapter_no"] == 5
    assert restored["sections"][0]["section_no"] == 6
    assert restored["sections"][0]["key_events"][0]["event_id"] == event[
        "event_id"
    ]
    assert restored["sections"][0]["subsections"][0]["state_change"] == (
        restored["sections"][0]["subsections"][0]["key_events"][0]
    )


def test_outline_review_unit_retries_with_exact_validation_feedback(
    make_context,
) -> None:
    context, trace = make_context()

    class UnitProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def generate_json(self, **kwargs) -> GenerationResponse:
            self.prompts.append(kwargs["prompt"])
            sections = [1] if len(self.prompts) == 1 else [1, 2]
            return GenerationResponse(
                data={
                    "review_report": {
                        "passed": True,
                        "score": 100,
                        "repair_scope": "none",
                        "findings": [],
                    },
                    "chapter": {"sections": sections},
                },
                model="test-model",
                input_tokens=10,
                output_tokens=20,
            )

    provider = UnitProvider()
    context.text_generation_provider = provider

    def require_two_sections(data):
        count = len(data["chapter"]["sections"])
        if count != 2:
            raise ValueError(f"returned {count} sections; expected 2")
        return data["chapter"]

    result, metrics = ReviewOutlineStep()._generate_review_unit(
        context=context,
        unit_key="chapter-003",
        prompt="review chapter 3",
        response_schema={"type": "object"},
        response_name="chapter_3",
        transform=require_two_sections,
    )

    assert result["value"]["sections"] == [1, 2]
    assert metrics["input_tokens"] == 20
    assert len(provider.prompts) == 2
    assert "returned 1 sections; expected 2" in provider.prompts[1]
    assert "Return the complete requested unit" in provider.prompts[1]
    failed = [
        event
        for event in trace.events
        if event.get("event") == "outline_review_unit_failed"
    ]
    assert failed[0]["unit"] == "chapter-003"


def test_non_retryable_unit_failure_stops_outer_step_retry() -> None:
    plans = [AttemptPlan("initial", 1), AttemptPlan("fallback", 1)]

    assert StepExecutionEngine._next_plan_index(
        plans=plans,
        current_index=0,
        preferred_phase="none",
    ) is None
    assert ReviewSectionsStep().retry_phase_for_error(
        ConsistencyCheckError("reviewed section is too short")
    ) == "none"


def test_insufficient_quota_is_non_retryable_but_rate_limit_is_retryable() -> None:
    class ProviderError(RuntimeError):
        def __init__(self, code: str) -> None:
            super().__init__(f"provider failed: {{'code': '{code}'}}")
            self.code = code
            self.body = {"error": {"code": code}}

    assert StepExecutionEngine._is_non_retryable_provider_error(
        ProviderError("insufficient_quota")
    )
    assert not StepExecutionEngine._is_non_retryable_provider_error(
        ProviderError("rate_limit_exceeded")
    )


def test_section_review_restores_section_specific_completed_event_ids() -> None:
    original = {
        "chapter_no": 1,
        "section_no": 2,
        "section_title": "時計が止まった時刻",
    }
    reviewed = {
        "chapter_no": 1,
        "section_no": 2,
        "section_title": "reviewer changed title",
        "state_updates": {
            "completed_event_ids": [
                "phase-1-beat-1",
                "phase-1-beat-2",
                "phase-1-beat-3",
            ]
        },
    }
    outline = {
        "key_events": [
            {"event_id": "phase-2-beat-1"},
            {"event_id": "phase-2-beat-2"},
            {"event_id": "phase-2-beat-3"},
        ]
    }

    restored = ReviewSectionsStep._restore_section_contract(
        reviewed,
        original_section=original,
        outline_section=outline,
    )

    assert restored["section_title"] == "時計が止まった時刻"
    assert restored["state_updates"]["completed_event_ids"] == [
        "phase-2-beat-1",
        "phase-2-beat-2",
        "phase-2-beat-3",
    ]


def test_outline_review_aligns_thread_lifecycle_to_ledger_events() -> None:
    def updates(*, opened=(), resolved=()):
        return {
            "opened_thread_ids": list(opened),
            "resolved_thread_ids": list(resolved),
            "planted_clue_ids": [],
            "paid_off_clue_ids": [],
            "character_arc_changes": [],
        }

    outline = {
        "story_plan": {
            "plot_threads": [
                {
                    "thread_id": "T09",
                    "open_event_id": "phase-1-beat-3",
                    "resolve_event_id": "phase-24-beat-2",
                }
            ],
            "foreshadowing": [],
            "character_arcs": [],
        },
        "chapters": [
            {
                "sections": [
                    {
                        "subsections": [
                            {
                                "key_events": [{"event_id": "phase-1-beat-1"}],
                                "planned_state_updates": updates(opened=("T09",)),
                            },
                            {
                                "key_events": [{"event_id": "phase-1-beat-3"}],
                                "planned_state_updates": updates(),
                            },
                            {
                                "key_events": [{"event_id": "phase-22-beat-2"}],
                                "planned_state_updates": updates(resolved=("T09",)),
                            },
                            {
                                "key_events": [{"event_id": "phase-24-beat-2"}],
                                "planned_state_updates": updates(resolved=("T09",)),
                            },
                        ]
                    }
                ]
            }
        ],
    }

    aligned = ReviewOutlineStep._align_plan_transitions(outline)
    subsections = aligned["chapters"][0]["sections"][0]["subsections"]

    assert subsections[0]["planned_state_updates"]["opened_thread_ids"] == []
    assert subsections[1]["planned_state_updates"]["opened_thread_ids"] == ["T09"]
    assert subsections[2]["planned_state_updates"]["resolved_thread_ids"] == []
    assert subsections[3]["planned_state_updates"]["resolved_thread_ids"] == ["T09"]


def test_scenario_body_length_can_be_overridden(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "scenario_body_generation": {
                    "target_characters": 2500,
                    "min_characters": 2200,
                    "max_characters": 2800,
                }
            }
        ),
        encoding="utf-8",
    )

    conf = load_config(str(cfg_path))

    assert conf.scenario_body_generation.target_characters == 2500
    assert conf.scenario_body_generation.min_characters == 2200
    assert conf.scenario_body_generation.max_characters == 2800


def test_scenario_body_target_must_be_within_acceptance_range(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "scenario_body_generation": {
                    "target_characters": 4000,
                    "min_characters": 2800,
                    "max_characters": 3800,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="character limits"):
        load_config(str(cfg_path))


def test_p1_config_rejects_expression_sheet_dimensions_not_divisible_by_four(
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps({"image_generation": {"expression_sheet_width": 2047}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="divisible by 4"):
        load_config(str(cfg_path))


def test_p1_cli_like_integration_creates_core_outputs(make_context) -> None:
    """P1: minimal integration should create state/trace/artifact files and support second-run skip."""
    context, _ = make_context()
    step = ConstantOutputStep("step-int", {"integrated": True})
    engine = StepExecutionEngine([step])

    out1 = engine.run(context)
    out2 = engine.run(context)

    assert out1["integrated"] is True
    assert out2["integrated"] is True
    assert Path(context.artifacts_dir, "step-int.json").exists()
    assert context.state_store.state_path.exists()
    trace_path = context.state_store.state_path.parent / context.config.trace_file_name
    assert trace_path.exists()


def test_schema_invalid_output_is_rejected_and_retried(make_context, base_config) -> None:
    """Invalid generated output must enter the existing regeneration loop."""
    context, trace = make_context()
    step = ConstantOutputStep(
        "step-01-generate-character-profiles",
        {"character_profiles": [{"character_id": "c001"}]},
    )
    step.schema_name = "step-01-generate-character-profiles.schema.json"
    step.input_keys = ("character_overviews",)
    engine = StepExecutionEngine([step])

    with pytest.raises(RuntimeError, match="Step failed"):
        engine.run(context)

    expected_attempts = (
        1
        + base_config.retry_strategy.short_retries
        + base_config.retry_strategy.prompt_revision_retries
        + int(base_config.retry_strategy.fallback_enabled)
    )
    assert step.calls == expected_attempts
    assert not Path(context.artifacts_dir, f"{step.name}.json").exists()
    failures = [event for event in trace.events if event.get("event") == "step_failed"]
    assert len(failures) == expected_attempts
    assert all("Schema validation failed" in str(event["failure_reason"]) for event in failures)


def test_minimal_steps_produce_schema_valid_outputs(make_context) -> None:
    context, _ = make_context()

    output = StepExecutionEngine(build_minimal_steps()).run(context)

    assert "character_profiles" in output
    assert "scenario_outline" in output
    assert "character_image_assets" in output
    assert "scenario_sections" in output
    assert "story_plan" in output["scenario_outline"]

    outline_sections = [
        section
        for chapter in output["scenario_outline"]["chapters"]
        for section in chapter["sections"]
    ]
    assert len({section["section_purpose"] for section in outline_sections}) == len(
        outline_sections
    )
    assert len(
        {
            tuple(event["event_id"] for event in section["key_events"])
            for section in outline_sections
        }
    ) == len(outline_sections)
    assert all(
        "planned_state_updates" in subsection
        for section in outline_sections
        for subsection in section["subsections"]
    )
    assert all(section["scene_location"] for section in outline_sections)
    assert all(section["scene_activity"] for section in outline_sections)
    assert all(section["scene_phase"] for section in outline_sections)
    assert all(
        [item["character_id"] for item in section["participant_presence"]]
        == section["participating_characters"]
        for section in outline_sections
    )


def test_outline_rejects_inconsistent_character_presence(make_context) -> None:
    context, _ = make_context()
    context.shared_data["input"]["scenario_idea"]["target_length"] = {
        "chapter_count": 1,
        "sections_per_chapter": 2,
    }
    output = StepExecutionEngine(build_minimal_steps()).run(context)
    outline = deepcopy(output["scenario_outline"])
    first_presence = outline["chapters"][0]["sections"][0][
        "participant_presence"
    ][0]
    first_presence["first_appearance"] = False

    with pytest.raises(ConsistencyCheckError, match="inconsistent first_appearance"):
        PipelineConsistencyChecker().check(
            context.shared_data,
            {"scenario_outline": outline},
        )

    outline = deepcopy(output["scenario_outline"])
    recurring_presence = outline["chapters"][0]["sections"][1][
        "participant_presence"
    ][0]
    recurring_presence["location_status_before"] = "known"
    recurring_presence["location_before"] = "an impossible prior location"

    with pytest.raises(ConsistencyCheckError, match="does not match last in-person"):
        PipelineConsistencyChecker().check(
            context.shared_data,
            {"scenario_outline": outline},
        )

    outline = deepcopy(output["scenario_outline"])
    for item in outline["chapters"][0]["sections"][0]["participant_presence"]:
        item["participation_status"] = "observing"

    with pytest.raises(ConsistencyCheckError, match="at least one active participant"):
        PipelineConsistencyChecker().check(
            context.shared_data,
            {"scenario_outline": outline},
        )


def test_scenario_body_quality_checks_length_and_required_events(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(build_minimal_steps()).run(context)
    first_section = output["scenario_sections"][0]
    combined = "".join(block["text"] for block in first_section["narrative_blocks"])
    character_count = sum(not character.isspace() for character in combined)

    body_config = context.config.scenario_body_generation
    subsection_count = body_config.subsections_per_section
    assert (
        body_config.min_characters * subsection_count
        <= character_count
        <= body_config.max_characters * subsection_count
    )
    required_events = output["scenario_outline"]["chapters"][0]["sections"][0]["key_events"]
    required_event_ids = {event["event_id"] for event in required_events}
    assert not any(event_id in combined for event_id in required_event_ids)
    assert required_event_ids == set(
        first_section["state_updates"]["completed_event_ids"]
    )

    invalid_output = deepcopy(output)
    invalid_output["scenario_sections"][0]["narrative_blocks"][0]["text"] = "too short"
    invalid_output["scenario_sections"][0]["narrative_blocks"][1]["text"] = "short"
    consistency_data = {
        **output,
        "_scenario_body_generation_config": {
            "min_characters": body_config.min_characters * subsection_count,
            "max_characters": body_config.max_characters * subsection_count,
            "min_dialogue_blocks": body_config.min_dialogue_blocks * subsection_count,
            "max_dialogue_blocks": body_config.max_dialogue_blocks * subsection_count,
            "require_event_mentions": True,
        },
    }
    with pytest.raises(ConsistencyCheckError, match="body length"):
        PipelineConsistencyChecker().check(
            consistency_data,
            {"scenario_sections": invalid_output["scenario_sections"]},
        )

    missing_event_output = deepcopy(output)
    missing_event_output["scenario_sections"][0]["state_updates"][
        "completed_event_ids"
    ] = []
    with pytest.raises(ConsistencyCheckError, match="does not complete required event IDs"):
        PipelineConsistencyChecker().check(
            consistency_data,
            {"scenario_sections": missing_event_output["scenario_sections"]},
        )

    missing_location_output = deepcopy(output)
    missing_location_output["scenario_sections"][0]["state_updates"][
        "character_locations"
    ] = []
    with pytest.raises(ConsistencyCheckError, match="does not record scene location"):
        PipelineConsistencyChecker().check(
            consistency_data,
            {"scenario_sections": missing_location_output["scenario_sections"]},
        )


def test_outline_rejects_foreshadow_payoff_before_plant(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(build_minimal_steps()).run(context)
    outline = deepcopy(output["scenario_outline"])
    event_id = outline["chapters"][0]["sections"][0]["key_events"][0]["event_id"]
    outline["story_plan"]["foreshadowing"] = [
        {
            "clue_id": "clue-1",
            "description": "A clue with an impossible lifecycle.",
            "plant_event_id": event_id,
            "payoff_event_id": event_id,
        }
    ]

    with pytest.raises(ConsistencyCheckError, match="must pay off after"):
        PipelineConsistencyChecker().check(
            context.shared_data,
            {"scenario_outline": outline},
        )

def test_mock_section_generation_advances_without_replaying_previous_event(make_context) -> None:
    context, _ = make_context()
    context.shared_data["input"]["scenario_idea"]["target_length"] = {
        "chapter_count": 1,
        "sections_per_chapter": 2,
    }

    output = StepExecutionEngine(build_minimal_steps()).run(context)

    first_event = output["scenario_outline"]["chapters"][0]["sections"][0][
        "key_events"
    ][0]
    second_text = "".join(
        block["text"] for block in output["scenario_sections"][1]["narrative_blocks"]
    )
    assert first_event["event_id"] not in second_text
    second_event = output["scenario_outline"]["chapters"][0]["sections"][1][
        "key_events"
    ][0]
    assert second_event["event_id"] not in second_text
    assert second_event["event_id"] in output["scenario_sections"][1][
        "state_updates"
    ]["completed_event_ids"]


def test_character_contradiction_enters_retry_strategy(make_context, base_config) -> None:
    context, trace = make_context()
    step = ContradictingProfileStep()

    with pytest.raises(RuntimeError, match="Step failed"):
        StepExecutionEngine([step]).run(context)

    expected_attempts = (
        1
        + base_config.retry_strategy.short_retries
        + base_config.retry_strategy.prompt_revision_retries
        + int(base_config.retry_strategy.fallback_enabled)
    )
    assert step.calls == expected_attempts
    failures = [event for event in trace.events if event.get("event") == "step_failed"]
    assert all("inconsistent name" in str(event["failure_reason"]) for event in failures)


def test_consistency_checker_rejects_timeline_drift(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(build_minimal_steps()).run(context)
    drifted_sections = deepcopy(output["scenario_sections"])
    drifted_sections[0]["section_title"] = "different-title"

    with pytest.raises(ConsistencyCheckError, match="section timeline differs"):
        PipelineConsistencyChecker().check(
            output,
            {"scenario_sections": drifted_sections},
        )


def test_character_profile_preserves_enriched_input(make_context) -> None:
    context, _ = make_context()
    overview = context.shared_data["input"]["character_overviews"][0]
    overview.update(
        {
            "age_range": "18歳",
            "gender": "female",
            "position": "見習い司書",
            "appearance_hint": "濃紺の髪と青灰色の瞳",
            "costume": "紺色の司書ローブ",
            "standing_pose": "記録帳を胸元で抱える",
            "image_prompt_hint": "静かな好奇心が伝わる表情",
            "background_hint": "失われた記憶を探している",
            "personality_traits": ["慎重", "観察力が高い"],
            "values": ["本人の意思"],
            "strengths": ["矛盾の発見"],
            "weaknesses": ["自分を疑いすぎる"],
            "relationship_to_protagonist": "本人",
            "conversation_role": "質問役",
            "growth_arc": "自分で選択できるようになる",
            "speech_profile": {
                "style": "柔らかな口語",
                "sentence_length": "短め",
                "politeness_level": "基本は丁寧",
                "first_person": "私",
                "second_person": "名前呼び",
                "common_phrases": ["少し待って"],
                "forbidden_phrases": ["どうでもいい"],
                "sample_lines": ["記録と合わない。"],
            },
            "relationships": [],
            "expression_rules": [
                {"condition": "考えを整理する", "expression": "thinking"}
            ],
        }
    )

    profile = GenerateCharacterProfilesStep().run(context).output[
        "character_profiles"
    ][0]

    assert profile["personality"]["core_traits"] == ["慎重", "観察力が高い"]
    assert profile["personality"]["strengths"] == ["矛盾の発見"]
    assert profile["speech"]["first_person"] == "私"
    assert profile["speech"]["sample_lines"] == ["記録と合わない。"]
    assert profile["appearance"]["costume"] == "紺色の司書ローブ"
    assert profile["background"] == "失われた記憶を探している"
    assert profile["narrative"]["growth_arc"] == "自分で選択できるようになる"
    assert profile["emotion_model"]["expression_rules"] == [
        {"condition": "考えを整理する", "expression": "thinking"}
    ]


def test_ai_character_profiles_pause_for_review_and_resume(make_context) -> None:
    context, trace = make_context()
    profiles = GenerateCharacterProfilesStep().run(context).output[
        "character_profiles"
    ]
    for profile in profiles:
        profile["speech"]["sample_lines"] = ["line one", "line two", "line three"]
    context.config.character_profile_generation.enabled = True
    context.config.character_profile_generation.require_review = True
    context.text_generation_provider = MockTextGenerationProvider(
        [{"character_profiles": profiles}]
    )
    engine = StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateOutlineStep()]
    )

    first_output = engine.run(context)

    assert context.paused_after_step == "step-01-generate-character-profiles"
    assert "character_profiles" in first_output
    assert "scenario_outline" not in first_output
    assert any(
        event.get("event") == "pipeline_paused_for_review"
        for event in trace.events
    )

    resumed_output = engine.run(
        context,
        options=ExecutionOptions(from_step="step-02-generate-outline"),
    )

    assert context.paused_after_step is None
    assert "scenario_outline" in resumed_output


def test_free_form_planning_input_pauses_and_resumes_at_step_01(make_context) -> None:
    context, _ = make_context(include_input=False)
    generated_input = {
        "scenario_idea": {
            "title": "Clock Library",
            "genre": "fantasy",
            "theme": "trust",
            "premise": "Two librarians investigate disappearing history.",
            "tone": "mysterious but hopeful",
            "must_include": ["a damaged book"],
            "must_avoid": ["graphic violence"],
            "audience": "young adults",
            "target_length": {"chapter_count": 1, "sections_per_chapter": 1},
        },
        "character_overviews": [
            {
                "character_id": "c001",
                "name": "Aoi",
                "role": "apprentice librarian",
                "summary": "A careful apprentice who has lost part of her memory.",
                "age_range": "18 years old",
                "gender": "female",
                "position": "apprentice librarian",
                "speech_style_hint": "careful and conversational",
                "appearance_hint": "dark blue hair and gray-blue eyes",
                "costume": "a navy librarian robe",
                "standing_pose": "holds a notebook at her chest",
                "image_prompt_hint": "young apprentice librarian in a navy robe",
                "background_hint": "She lost part of her childhood memory.",
                "personality_traits": ["observant", "cautious"],
                "values": ["truth", "personal choice"],
                "strengths": ["notices contradictions"],
                "weaknesses": ["doubts her judgment"],
                "relationship_to_protagonist": "self",
                "conversation_role": "asks questions and identifies clues",
                "growth_arc": "learns to trust her own choices",
                "speech_profile": {
                    "style": "soft natural speech",
                    "sentence_length": "short to medium",
                    "politeness_level": "usually polite",
                    "first_person": "I",
                    "second_person": "name or role",
                    "common_phrases": ["Wait a moment"],
                    "forbidden_phrases": ["I do not care"],
                    "sample_lines": [
                        "This page feels different.",
                        "I want to check the facts first.",
                        "Please let me make this choice.",
                    ],
                },
                "relationship_hints": [],
                "relationships": [],
                "emotion_range": ["neutral", "thinking", "determined"],
                "expression_rules": [
                    {
                        "condition": "examining a contradiction",
                        "expression": "thinking",
                    }
                ],
            }
        ],
    }
    context.shared_data["rough_idea"] = "A fantasy story set in a time library."
    context.config.planning_input_generation.enabled = True
    context.config.planning_input_generation.require_review = True
    context.text_generation_provider = MockTextGenerationProvider([generated_input])
    engine = StepExecutionEngine(
        [GeneratePlanningInputStep(), GenerateCharacterProfilesStep()]
    )

    output = engine.run(context)

    assert context.paused_after_step == "step-00-generate-planning-input"
    assert output["input"] == generated_input
    assert "character_profiles" not in output

    resumed = engine.run(
        context,
        options=ExecutionOptions(from_step="step-01-generate-character-profiles"),
    )

    assert context.paused_after_step is None
    assert resumed["input"] == generated_input
    assert resumed["character_profiles"][0]["character_id"] == "c001"


def test_consistency_checker_rejects_ambiguous_character_names(make_context) -> None:
    context, _ = make_context()
    context.shared_data["input"]["character_overviews"].append(
        {
            "character_id": "c002",
            "name": "Ｎ",
            "role": "other",
            "summary": "s",
        }
    )
    profiles = GenerateCharacterProfilesStep().run(context).output["character_profiles"]

    with pytest.raises(ConsistencyCheckError, match="ambiguous character naming"):
        PipelineConsistencyChecker().check(
            context.shared_data,
            {"character_profiles": profiles},
        )


def test_outline_plans_gradual_cast_and_honors_chapter_scopes() -> None:
    roles = [
        ("c001", "主人公"),
        ("c002", "助手"),
        ("c003", "師匠・導き手"),
        ("c004", "ライバル検事"),
        ("c005", "刑事"),
        ("c006", "親友"),
        ("c007", "第1〜4話の中核敵対者"),
        ("c008", "第5話の被告人"),
        ("c009", "第5話助手"),
        ("c010", "第5話の真犯人"),
    ]
    profiles = [
        {
            "character_id": character_id,
            "role": role,
            "narrative": {
                "conversation_role": role,
                "relationship_to_protagonist": (
                    "本人" if character_id == "c001" else "関係者"
                ),
            },
        }
        for character_id, role in roles
    ]

    planned = GenerateOutlineStep._plan_participation(
        profiles,
        chapter_count=5,
        sections_per_chapter=6,
    )

    assert planned[(1, 1)] == ["c001", "c002"]
    assert all(
        character_id not in planned[(chapter_no, section_no)]
        for character_id in ("c008", "c009", "c010")
        for chapter_no in range(1, 5)
        for section_no in range(1, 7)
    )
    assert "c008" in planned[(5, 1)]
    assert "c009" not in planned[(5, 1)]
    appeared = {
        character_id
        for participants in planned.values()
        for character_id in participants
    }
    assert appeared == {character_id for character_id, _ in roles}


def test_outline_never_recycles_events_to_fill_extra_subsections() -> None:
    with pytest.raises(ValueError, match="recycling completed events"):
        GenerateOutlineStep._events_for_subsection(
            [
                {"event_id": "phase-1-beat-1", "description": "Event one."},
                {"event_id": "phase-1-beat-2", "description": "Event two."},
                {"event_id": "phase-1-beat-3", "description": "Event three."},
            ],
            count=6,
            subsection_no=4,
        )


def test_section_progress_rejects_repeated_continuity_summary() -> None:
    previous_state = {
        "occurred_events": [
            {
                "event_id": "phase-1-beat-1",
                "description": "Completed setup.",
            }
        ],
        "recent_context": "The pair waits outside and reviews the interview plan.",
    }
    generated = {
        "state_updates": {
            "continuity_summary": (
                "The pair waits outside and reviews the interview plan."
            )
        }
    }

    with pytest.raises(ValueError, match="too similar to the previous scene"):
        GenerateSectionsStep._validate_forward_progress(
            generated,
            previous_state=previous_state,
            target_section={
                "key_events": [
                    {
                        "event_id": "phase-1-beat-2",
                        "description": "Begin the interview.",
                    }
                ]
            },
        )


def test_section_progress_rejects_completed_event_in_new_body() -> None:
    generated = {
        "narrative_blocks": [
            {
                "text": "The scene exposes phase-1-beat-1 before moving on."
            }
        ],
        "state_updates": {"continuity_summary": "The interview begins."},
    }

    with pytest.raises(ValueError, match="exposes internal event IDs"):
        GenerateSectionsStep._validate_forward_progress(
            generated,
            previous_state={
                "occurred_events": [
                    {
                        "event_id": "phase-1-beat-1",
                        "description": "Completed setup.",
                    }
                ],
                "recent_context": "The setup is finished.",
            },
            target_section={
                "key_events": [
                    {
                        "event_id": "phase-1-beat-2",
                        "description": "Begin the interview.",
                    }
                ]
            },
        )


def test_temperature_policy_limits_diversity_to_selected_steps(make_context) -> None:
    context, trace = make_context()

    StepExecutionEngine(build_minimal_steps()).run(context)

    succeeded = {
        event["step"]: event
        for event in trace.events
        if event.get("event") == "step_succeeded"
    }
    assert succeeded["step-01-generate-character-profiles"]["temperature"] == 0.2
    assert succeeded["step-01-generate-character-profiles"]["temperature_mode"] == "deterministic"
    assert succeeded["step-02-generate-outline"]["temperature"] == 0.7
    assert succeeded["step-02-generate-outline"]["temperature_mode"] == "diversity"
    assert succeeded["step-03-generate-character-images"]["temperature"] == 0.2
    assert (
        succeeded["step-03-generate-character-images"]["temperature_mode"]
        == "deterministic"
    )
    assert succeeded["step-04-generate-sections"]["temperature"] == 0.7
    assert succeeded["step-04-generate-sections"]["temperature_mode"] == "diversity"
    assert succeeded["step-05-generate-dialogue-tags"]["temperature"] == 0.2
    assert (
        succeeded["step-05-generate-dialogue-tags"]["temperature_mode"]
        == "deterministic"
    )


def test_temperature_policy_rejects_step_override(make_context) -> None:
    context, trace = make_context()

    with pytest.raises(RuntimeError, match="Step failed"):
        StepExecutionEngine([TemperatureBypassStep()]).run(context)

    failures = [event for event in trace.events if event.get("event") == "step_failed"]
    assert failures
    assert all("Temperature policy violation" in event["failure_reason"] for event in failures)


def test_prompt_catalog_supports_pinned_versions_and_hashes() -> None:
    catalog = PromptCatalog()

    v1 = catalog.resolve("step-02-generate-outline", "v1")
    v2 = catalog.resolve("step-02-generate-outline", "v2")

    assert v1.version == "v1"
    assert v2.version == "v2"
    assert v1.text != v2.text
    assert v1.content_hash != v2.content_hash


def test_pipeline_trace_records_prompt_version_and_hash(make_context) -> None:
    context, trace = make_context()
    context.config.prompt_versions = {
        "step-01-generate-character-profiles": "v1",
        "step-02-generate-outline": "v1",
        "step-03-generate-character-images": "v1",
        "step-04-generate-sections": "v2",
    }

    steps = build_minimal_steps()
    StepExecutionEngine(steps).run(context)

    succeeded = [event for event in trace.events if event.get("event") == "step_succeeded"]
    assert len(succeeded) == len(steps)
    versions = {event["step"]: event["prompt_version"] for event in succeeded}
    assert versions == {
        "step-01-generate-character-profiles": "v1",
        "step-02-generate-outline": "v1",
        "step-03-generate-character-images": "v1",
        "step-04-generate-sections": "v2",
        "step-05-generate-dialogue-tags": None,
        "step-06-render-html": None,
    }
    prompt_backed = [
        event for event in succeeded if event["prompt_hash"] is not None
    ]
    assert all(len(event["prompt_hash"]) == 64 for event in prompt_backed)
    deterministic_steps = {
        event["step"]: event
        for event in succeeded
        if event["step"] in {"step-05-generate-dialogue-tags", "step-06-render-html"}
    }
    assert all(event["prompt_hash"] is None for event in deterministic_steps.values())


def test_prompt_impact_report_compares_run_metrics(tmp_path: Path) -> None:
    baseline = tmp_path / "run-baseline"
    candidate = tmp_path / "run-candidate"
    for run_root, version, prompt_hash, duration, artifact in (
        (baseline, "v1", "hash-v1", 100, '{"value": 1}'),
        (candidate, "v2", "hash-v2", 130, '{"value": 2}'),
    ):
        (run_root / "artifacts").mkdir(parents=True)
        events = [
            {"step": "step-a", "event": "step_started"},
            {
                "step": "step-a",
                "event": "step_succeeded",
                "prompt_version": version,
                "prompt_hash": prompt_hash,
                "duration_ms": duration,
                "input_tokens": 10,
                "output_tokens": 20,
            },
        ]
        (run_root / "trace.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
        (run_root / "artifacts" / "step-a.json").write_text(artifact, encoding="utf-8")

    report = PromptImpactReporter().compare(baseline, candidate)
    step = report["steps"]["step-a"]

    assert step["prompt_changed"] is True
    assert step["baseline_prompt_version"] == "v1"
    assert step["candidate_prompt_version"] == "v2"
    assert step["duration_ms_delta"] == 30
    assert step["artifact_changed"] is True
