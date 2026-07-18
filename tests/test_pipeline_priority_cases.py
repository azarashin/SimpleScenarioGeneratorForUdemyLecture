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
from pipeline.engine import ExecutionOptions, StepExecutionEngine
from pipeline.prompt_impact import PromptImpactReporter
from pipeline.prompts import PromptCatalog
from pipeline.steps import GenerateCharacterProfilesStep, build_minimal_steps
from pipeline.state import StepState
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

    outline_sections = [
        section
        for chapter in output["scenario_outline"]["chapters"]
        for section in chapter["sections"]
    ]
    assert len({section["section_purpose"] for section in outline_sections}) == len(
        outline_sections
    )
    assert len({tuple(section["key_events"]) for section in outline_sections}) == len(
        outline_sections
    )


def test_scenario_body_quality_checks_length_and_required_events(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(build_minimal_steps()).run(context)
    first_section = output["scenario_sections"][0]
    combined = "".join(block["text"] for block in first_section["narrative_blocks"])
    character_count = sum(not character.isspace() for character in combined)

    assert 800 <= character_count <= 1600
    required_events = output["scenario_outline"]["chapters"][0]["sections"][0]["key_events"]
    assert all(event in combined for event in required_events)

    invalid_output = deepcopy(output)
    invalid_output["scenario_sections"][0]["narrative_blocks"][0]["text"] = "too short"
    invalid_output["scenario_sections"][0]["narrative_blocks"][1]["text"] = "short"
    consistency_data = {
        **output,
        "_scenario_body_generation_config": {
            "min_characters": 800,
            "max_characters": 1600,
            "require_event_mentions": True,
        },
    }
    with pytest.raises(ConsistencyCheckError, match="body length"):
        PipelineConsistencyChecker().check(
            consistency_data,
            {"scenario_sections": invalid_output["scenario_sections"]},
        )

    missing_event_output = deepcopy(output)
    missing_event = required_events[0]
    for block in missing_event_output["scenario_sections"][0]["narrative_blocks"]:
        block["text"] = block["text"].replace(missing_event, "covered event")
    with pytest.raises(ConsistencyCheckError, match="does not cover required events"):
        PipelineConsistencyChecker().check(
            consistency_data,
            {"scenario_sections": missing_event_output["scenario_sections"]},
        )


def test_mock_section_generation_carries_previous_section_state(make_context) -> None:
    context, _ = make_context()
    context.shared_data["input"]["scenario_idea"]["target_length"] = {
        "chapter_count": 1,
        "sections_per_chapter": 2,
    }

    output = StepExecutionEngine(build_minimal_steps()).run(context)

    first_event = output["scenario_outline"]["chapters"][0]["sections"][0]["key_events"][0]
    second_text = "".join(
        block["text"] for block in output["scenario_sections"][1]["narrative_blocks"]
    )
    assert first_event in second_text


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

    StepExecutionEngine(build_minimal_steps()).run(context)

    succeeded = [event for event in trace.events if event.get("event") == "step_succeeded"]
    assert len(succeeded) == 4
    versions = {event["step"]: event["prompt_version"] for event in succeeded}
    assert versions == {
        "step-01-generate-character-profiles": "v1",
        "step-02-generate-outline": "v1",
        "step-03-generate-character-images": "v1",
        "step-04-generate-sections": "v2",
    }
    assert all(len(event["prompt_hash"]) == 64 for event in succeeded)


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
