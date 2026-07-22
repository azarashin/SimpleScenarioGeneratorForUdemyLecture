from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .errors import ConsistencyCheckError, is_non_retryable_provider_error
from .scenario_quality import ScenarioBodyQualityChecker
from .text_generation import _scenario_section_response_schema
from .types import Step, StepContext, StepResult


class OutlineReviewUnitExhausted(RuntimeError):
    """Raised after one outline review unit has exhausted feedback retries."""


def _review_report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["passed", "score", "repair_scope", "findings"],
        "additionalProperties": False,
        "properties": {
            "passed": {"type": "boolean"},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "repair_scope": {
                "type": "string",
                "enum": ["none", "local", "section", "outline"],
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "severity",
                        "category",
                        "block_ids",
                        "problem",
                        "repair_instruction",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["info", "warning", "critical"],
                        },
                        "category": {"type": "string", "minLength": 1},
                        "block_ids": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "problem": {"type": "string", "minLength": 1},
                        "repair_instruction": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }


def _inline_common_refs(schema: dict[str, Any], schemas_dir: Path) -> dict[str, Any]:
    common = json.loads((schemas_dir / "common.schema.json").read_text(encoding="utf-8"))
    local_defs = schema.get("$defs", {})

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        common_prefix = "common.schema.json#/$defs/"
        local_prefix = "#/$defs/"
        if isinstance(ref, str) and ref.startswith(common_prefix):
            replacement = deepcopy(common["$defs"][ref[len(common_prefix) :]])
            replacement.update({key: visit(item) for key, item in value.items() if key != "$ref"})
            return visit(replacement)
        if isinstance(ref, str) and ref.startswith(local_prefix):
            replacement = deepcopy(local_defs[ref[len(local_prefix) :]])
            replacement.update({key: visit(item) for key, item in value.items() if key != "$ref"})
            return visit(replacement)
        result = {
            key: visit(item)
            for key, item in value.items()
            if key not in {"$schema", "$id", "$defs"}
        }
        properties = result.get("properties")
        if isinstance(properties, dict):
            result["required"] = list(properties)
            result["additionalProperties"] = False
        return result

    return visit(schema)


class ReviewOutlineStep(Step):
    name = "step-02-review-outline"
    schema_name = "step-02-review-outline.schema.json"
    input_keys = ("scenario_idea", "character_profiles", "scenario_outline")

    def run(self, context: StepContext) -> StepResult:
        schemas_dir = Path(__file__).resolve().parent.parent / "schemas"
        outline_schema = json.loads(
            (schemas_dir / "scenario-outline.schema.json").read_text(encoding="utf-8")
        )
        api_outline_schema = _inline_common_refs(outline_schema, schemas_dir)
        story_plan_schema = api_outline_schema["properties"]["story_plan"]
        chapter_schema = api_outline_schema["properties"]["chapters"]["items"]
        plan_response_schema = {
            "type": "object",
            "required": ["review_report", "story_plan"],
            "additionalProperties": False,
            "properties": {
                "review_report": _review_report_schema(),
                "story_plan": story_plan_schema,
            },
        }
        shared_input = context.shared_data["input"]
        profiles = context.shared_data["character_profiles"]
        draft_outline = context.shared_data["scenario_outline"]
        plan_prompt = (
            "Design and review the global story ledger before prose generation.\n"
            "This is a repair step, not a rejection-only audit. Generic placeholders, empty "
            "planning ledgers, and missing concrete story details are defects you MUST repair in "
            "the returned story_plan. Return passed=true after making all repairs that can be "
            "derived from PLANNING INPUT and CHARACTER PROFILES. Set "
            "passed=false only for a genuine contradiction in those authoritative inputs that "
            "cannot be resolved without a human decision.\n\n"
            f"PLANNING INPUT\n{json.dumps(shared_input, ensure_ascii=False, indent=2)}\n\n"
            f"CHARACTER PROFILES\n{json.dumps(profiles, ensure_ascii=False, indent=2)}\n\n"
            f"DRAFT OUTLINE EVENT MAP\n{json.dumps(self._event_map(draft_outline), ensure_ascii=False, indent=2)}\n\n"
            "Inventory every continuing plot thread, planted clue and payoff, and character "
            "arc. Use only event IDs from DRAFT OUTLINE EVENT MAP. Open/plant/turn events must "
            "precede resolve/payoff events. Cover the complete story, not only early chapters."
        )
        plan_data, plan_metrics = self._generate_review_unit(
            context=context,
            unit_key="story-plan",
            prompt=plan_prompt,
            response_schema=plan_response_schema,
            response_name="scenario_story_plan_review",
            transform=lambda data: self._validated_story_plan(data),
        )
        reports = [plan_data["review_report"]]
        story_plan = plan_data["value"]
        reviewed_chapters: list[dict[str, Any]] = []
        prompts = [plan_prompt]
        input_tokens = plan_metrics["input_tokens"]
        output_tokens = plan_metrics["output_tokens"]
        last_model = plan_metrics["model"]

        chapter_response_schema = {
            "type": "object",
            "required": ["review_report", "chapter"],
            "additionalProperties": False,
            "properties": {
                "review_report": _review_report_schema(),
                "chapter": chapter_schema,
            },
        }
        for draft_chapter in draft_outline["chapters"]:
            chapter_no = draft_chapter["chapter_no"]
            chapter_prompt = self._chapter_prompt(
                shared_input=shared_input,
                profiles=profiles,
                story_plan=story_plan,
                previous_chapter=(reviewed_chapters[-1] if reviewed_chapters else None),
                draft_chapter=draft_chapter,
            )
            chapter_data, chapter_metrics = self._generate_review_unit(
                context=context,
                unit_key=f"chapter-{chapter_no:03d}",
                prompt=chapter_prompt,
                response_schema=chapter_response_schema,
                response_name=f"scenario_outline_chapter_{chapter_no}_review",
                transform=lambda data, draft=draft_chapter, number=chapter_no: (
                    self._validated_chapter(data, draft, number)
                ),
            )
            reviewed_chapters.append(chapter_data["value"])
            reports.append(chapter_data["review_report"])
            prompts.append(chapter_prompt)
            input_tokens += chapter_metrics["input_tokens"]
            output_tokens += chapter_metrics["output_tokens"]
            last_model = chapter_metrics["model"]

        reviewed_outline = {
            "title": shared_input["scenario_idea"]["title"],
            "logline": shared_input["scenario_idea"]["premise"],
            "story_plan": story_plan,
            "chapters": reviewed_chapters,
        }
        reviewed_outline = self._align_plan_transitions(reviewed_outline)
        report = self._aggregate_reports(reports)
        combined_prompt = "\n\n--- NEXT OUTLINE REVIEW UNIT ---\n\n".join(prompts)
        return StepResult(
            output={
                "scenario_outline": reviewed_outline,
                "outline_review_report": report,
            },
            prompt=combined_prompt,
            prompt_version="v2",
            prompt_hash=hashlib.sha256(combined_prompt.encode("utf-8")).hexdigest(),
            model=last_model,
            temperature=context.config.temperature_for(self.name),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata={"review_unit_count": len(prompts)},
        )

    @staticmethod
    def _event_map(outline: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "chapter_no": chapter["chapter_no"],
                "section_no": section["section_no"],
                "event_ids": [event["event_id"] for event in section["key_events"]],
            }
            for chapter in outline["chapters"]
            for section in chapter["sections"]
        ]

    @staticmethod
    def _align_plan_transitions(outline: dict[str, Any]) -> dict[str, Any]:
        """Place thread/clue lifecycle IDs at their authoritative ledger events."""
        aligned = deepcopy(outline)
        updates_by_event: dict[str, dict[str, Any]] = {}
        arc_characters = {
            arc["character_id"] for arc in aligned["story_plan"]["character_arcs"]
        }
        arc_descriptions: dict[str, list[str]] = {
            character_id: [] for character_id in arc_characters
        }
        for chapter in aligned["chapters"]:
            for section in chapter["sections"]:
                for subsection in section.get("subsections", []):
                    updates = subsection["planned_state_updates"]
                    updates["opened_thread_ids"] = []
                    updates["resolved_thread_ids"] = []
                    updates["planted_clue_ids"] = []
                    updates["paid_off_clue_ids"] = []
                    for change in updates["character_arc_changes"]:
                        character_id = change["character_id"]
                        if character_id in arc_descriptions:
                            arc_descriptions[character_id].append(
                                change["description"]
                            )
                    updates["character_arc_changes"] = [
                        change
                        for change in updates["character_arc_changes"]
                        if change["character_id"] not in arc_characters
                    ]
                    for event in subsection["key_events"]:
                        updates_by_event[event["event_id"]] = updates

        for thread in aligned["story_plan"]["plot_threads"]:
            open_updates = updates_by_event.get(thread["open_event_id"])
            if open_updates is None:
                raise ValueError(
                    f"Plot thread {thread['thread_id']} references unknown open event "
                    f"{thread['open_event_id']}"
                )
            open_updates["opened_thread_ids"].append(
                thread["thread_id"]
            )
            if thread["resolve_event_id"] is not None:
                resolve_updates = updates_by_event.get(thread["resolve_event_id"])
                if resolve_updates is None:
                    raise ValueError(
                        f"Plot thread {thread['thread_id']} references unknown resolve "
                        f"event {thread['resolve_event_id']}"
                    )
                resolve_updates["resolved_thread_ids"].append(thread["thread_id"])
        for clue in aligned["story_plan"]["foreshadowing"]:
            plant_updates = updates_by_event.get(clue["plant_event_id"])
            payoff_updates = updates_by_event.get(clue["payoff_event_id"])
            if plant_updates is None or payoff_updates is None:
                raise ValueError(
                    f"Foreshadow {clue['clue_id']} references an unknown plant or "
                    "payoff event"
                )
            plant_updates["planted_clue_ids"].append(
                clue["clue_id"]
            )
            payoff_updates["paid_off_clue_ids"].append(
                clue["clue_id"]
            )
        for arc in aligned["story_plan"]["character_arcs"]:
            descriptions = arc_descriptions[arc["character_id"]]
            for index, event_id in enumerate(arc["turning_event_ids"]):
                turning_updates = updates_by_event.get(event_id)
                if turning_updates is None:
                    raise ValueError(
                        f"Character arc {arc['character_id']} references unknown turning "
                        f"event {event_id}"
                    )
                description = (
                    descriptions[index]
                    if index < len(descriptions)
                    else (
                        f"{arc['character_id']} changes from {arc['initial_state']} "
                        f"toward {arc['final_state']}."
                    )
                )
                turning_updates["character_arc_changes"].append(
                    {
                        "character_id": arc["character_id"],
                        "description": description,
                    }
                )
        return aligned

    @staticmethod
    def _require_passed(report: dict[str, Any], unit: str) -> None:
        if report["passed"]:
            return
        problems = "; ".join(item["problem"] for item in report["findings"])
        raise ValueError(f"Reviewed {unit} still has unresolved issues: {problems}")

    @classmethod
    def _validated_story_plan(cls, data: dict[str, Any]) -> dict[str, Any]:
        cls._require_passed(data["review_report"], "global story plan")
        return data["story_plan"]

    @classmethod
    def _validated_chapter(
        cls, data: dict[str, Any], draft: dict[str, Any], chapter_no: int
    ) -> dict[str, Any]:
        cls._require_passed(data["review_report"], f"chapter {chapter_no}")
        return cls._restore_chapter_identity(data["chapter"], draft)

    def _generate_review_unit(
        self,
        *,
        context: StepContext,
        unit_key: str,
        prompt: str,
        response_schema: dict[str, Any],
        response_name: str,
        transform: Callable[[dict[str, Any]], Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Retry and checkpoint one review unit with its exact validation feedback."""
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "prompt": prompt,
                    "model": context.config.text_generation.model,
                    "schema": response_schema,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        checkpoint_dir = Path(context.artifacts_dir) / "outline-review-units"
        checkpoint_path = checkpoint_dir / f"{unit_key}.json"
        if checkpoint_path.exists() and not context.force:
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if checkpoint.get("fingerprint") == fingerprint:
                    data = checkpoint["response"]
                    value = transform(data)
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": self.name,
                            "event": "outline_review_unit_checkpoint_loaded",
                            "unit": unit_key,
                        }
                    )
                    return {
                        "review_report": data["review_report"],
                        "value": value,
                    }, {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model": context.config.text_generation.model,
                    }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": self.name,
                        "event": "outline_review_unit_checkpoint_rejected",
                        "unit": unit_key,
                        "failure_reason": str(exc),
                    }
                )

        strategy = context.config.retry_strategy
        phases = ["initial"]
        phases.extend("short_retry" for _ in range(strategy.short_retries))
        phases.extend(
            "prompt_revision" for _ in range(strategy.prompt_revision_retries)
        )
        if strategy.fallback_enabled:
            phases.append("fallback")
        failure_reason = ""
        input_tokens = 0
        output_tokens = 0
        for attempt, phase in enumerate(phases, start=1):
            attempt_prompt = prompt
            if failure_reason:
                attempt_prompt += (
                    "\n\nPREVIOUS ATTEMPT VALIDATION FAILURE\n"
                    f"{failure_reason}\n\n"
                    "Correct this exact failure. Return the complete requested unit, not a "
                    "partial patch or summary. Recount every required array before responding."
                )
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": self.name,
                    "event": "outline_review_unit_started",
                    "unit": unit_key,
                    "unit_attempt": attempt,
                    "retry_phase": phase,
                    "previous_failure_reason": failure_reason or None,
                }
            )
            try:
                response = context.text_generation_provider.generate_json(
                    prompt=attempt_prompt,
                    model=context.config.text_generation.model,
                    temperature=context.config.temperature_for(self.name),
                    response_schema=response_schema,
                    response_name=response_name,
                )
                input_tokens += response.input_tokens or 0
                output_tokens += response.output_tokens or 0
                value = transform(response.data)
            except Exception as exc:  # noqa: BLE001
                failure_reason = str(exc)
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": self.name,
                        "event": "outline_review_unit_failed",
                        "unit": unit_key,
                        "unit_attempt": attempt,
                        "retry_phase": phase,
                        "failure_reason": failure_reason,
                    }
                )
                continue

            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_temp = checkpoint_path.with_suffix(".tmp")
            checkpoint_temp.write_text(
                json.dumps(
                    {"fingerprint": fingerprint, "response": response.data},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            checkpoint_temp.replace(checkpoint_path)
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": self.name,
                    "event": "outline_review_unit_succeeded",
                    "unit": unit_key,
                    "unit_attempt": attempt,
                    "retry_phase": phase,
                }
            )
            return {
                "review_report": response.data["review_report"],
                "value": value,
            }, {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": response.model,
            }

        raise OutlineReviewUnitExhausted(
            f"Outline review unit {unit_key} exhausted {len(phases)} attempts. "
            f"Last error: {failure_reason}"
        )

    def retry_phase_for_error(self, error: Exception) -> str | None:
        if isinstance(error, (OutlineReviewUnitExhausted, ConsistencyCheckError)):
            return "none"
        return None

    @staticmethod
    def _chapter_prompt(
        *,
        shared_input: dict[str, Any],
        profiles: list[dict[str, Any]],
        story_plan: dict[str, Any],
        previous_chapter: dict[str, Any] | None,
        draft_chapter: dict[str, Any],
    ) -> str:
        return (
            "Review and REWRITE exactly one scenario chapter before prose generation.\n"
            "This is a repair step, not a rejection-only audit. Rewrite every generic "
            "placeholder into a concrete, causally connected story event. Return passed=true "
            "after repairing all issues derivable from the authoritative inputs. Set "
            "passed=false only for an irreconcilable input contradiction.\n\n"
            f"PLANNING INPUT\n{json.dumps(shared_input, ensure_ascii=False, indent=2)}\n\n"
            f"CHARACTER PROFILES\n{json.dumps(profiles, ensure_ascii=False, indent=2)}\n\n"
            f"GLOBAL STORY PLAN\n{json.dumps(story_plan, ensure_ascii=False, indent=2)}\n\n"
            f"PREVIOUS REVIEWED CHAPTER\n{json.dumps(previous_chapter, ensure_ascii=False, indent=2)}\n\n"
            f"DRAFT CHAPTER\n{json.dumps(draft_chapter, ensure_ascii=False, indent=2)}\n\n"
            "Return this chapter only. Preserve its chapter number, every section and "
            "subsection count, all ordering, and every event_id. Preserve valid character "
            "IDs. Give every section a chapter-specific purpose and every subsection a "
            "distinct start state, irreversible state change, resulting state, and handoff. "
            "Populate planned_state_updates so every GLOBAL STORY PLAN thread opening, "
            "resolution, clue plant, clue payoff, and character turning point assigned to "
            "this chapter appears at its declared event ID. Ensure the first state follows "
            "PREVIOUS REVIEWED CHAPTER and later states never replay completed scenes. Do "
            "not invent an extra central case when PLANNING INPUT assigns cases to chapters. "
            "Character timing does not need to follow an original work or the order of the "
            "input source, but introductions must feel narratively earned. Introduce major "
            "characters gradually as the story needs them instead of assembling them all at "
            "the opening for convenience. Before placing a character in "
            "participating_characters, establish that character's first appearance in this "
            "or an earlier event. Give each first appearance and first direct conversation a "
            "clear story purpose, a reason the character is present, and enough context for "
            "the audience to understand the current relationship. If already-acquainted "
            "characters speak at their first on-page meeting, establish or reveal why they "
            "meet and what their relationship is before or during that event. Repair any "
            "premature participation, unexplained meeting, or relationship assumed before "
            "its introduction. A character may be foreshadowed by name or reputation before "
            "appearing, but that alone does not make the character a scene participant."
        )

    @staticmethod
    def _restore_chapter_identity(
        reviewed: dict[str, Any], draft: dict[str, Any]
    ) -> dict[str, Any]:
        """Restore structural IDs and reject incomplete chapter responses."""
        restored = deepcopy(reviewed)
        if len(restored["sections"]) != len(draft["sections"]):
            raise ValueError(
                f"Reviewed chapter {draft['chapter_no']} returned "
                f"{len(restored['sections'])} sections; expected {len(draft['sections'])}"
            )
        restored["chapter_no"] = draft["chapter_no"]
        for reviewed_section, draft_section in zip(
            restored["sections"], draft["sections"], strict=True
        ):
            reviewed_section["section_no"] = draft_section["section_no"]
            draft_subsections = draft_section.get("subsections", [])
            reviewed_subsections = reviewed_section.get("subsections", [])
            if len(reviewed_subsections) != len(draft_subsections):
                raise ValueError(
                    f"Reviewed chapter {draft['chapter_no']} section "
                    f"{draft_section['section_no']} returned {len(reviewed_subsections)} "
                    f"subsections; expected {len(draft_subsections)}"
                )
            if len(reviewed_section["key_events"]) != len(
                draft_section["key_events"]
            ):
                raise ValueError(
                    f"Reviewed chapter {draft['chapter_no']} section "
                    f"{draft_section['section_no']} changed the event count"
                )
            for reviewed_event, draft_event in zip(
                reviewed_section["key_events"],
                draft_section["key_events"],
                strict=True,
            ):
                reviewed_event["event_id"] = draft_event["event_id"]
            for reviewed_subsection, draft_subsection in zip(
                reviewed_subsections, draft_subsections, strict=True
            ):
                reviewed_subsection["subsection_no"] = draft_subsection[
                    "subsection_no"
                ]
                if len(reviewed_subsection["key_events"]) != len(
                    draft_subsection["key_events"]
                ):
                    raise ValueError(
                        f"Reviewed chapter {draft['chapter_no']} section "
                        f"{draft_section['section_no']} subsection "
                        f"{draft_subsection['subsection_no']} changed the event count"
                    )
                for reviewed_event, draft_event in zip(
                    reviewed_subsection["key_events"],
                    draft_subsection["key_events"],
                    strict=True,
                ):
                    reviewed_event["event_id"] = draft_event["event_id"]
                state_event_id = draft_subsection["state_change"]["event_id"]
                reviewed_subsection["state_change"] = deepcopy(
                    next(
                        event
                        for event in reviewed_subsection["key_events"]
                        if event["event_id"] == state_event_id
                    )
                )
        return restored

    @staticmethod
    def _aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
        scope_order = {"none": 0, "local": 1, "section": 2, "outline": 3}
        return {
            "passed": all(report["passed"] for report in reports),
            "score": min(report["score"] for report in reports),
            "repair_scope": max(
                (report["repair_scope"] for report in reports),
                key=scope_order.__getitem__,
            ),
            "findings": [
                finding
                for report in reports
                for finding in report["findings"]
            ],
        }

    @staticmethod
    def _restore_immutable_fields(
        outline: dict[str, Any], scenario_idea: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep identity fields authoritative even if a reviewer paraphrases them."""
        restored = deepcopy(outline)
        restored["title"] = scenario_idea["title"]
        restored["logline"] = scenario_idea["premise"]
        return restored

    def requires_review_after_success(self, context: StepContext) -> bool:
        return context.config.scenario_review.require_human_review


class ReviewSectionsStep(Step):
    name = "step-04-review-sections"
    schema_name = "step-04-review-sections.schema.json"
    input_keys = ("scenario_idea", "character_profiles", "scenario_outline", "scenario_sections")

    def retry_phase_for_error(self, error: Exception) -> str | None:
        # A post-review consistency failure will reproduce the same full reviewed
        # output; repeating every section is expensive and provides no new feedback.
        if isinstance(error, ConsistencyCheckError):
            return "none"
        return None

    def run(self, context: StepContext) -> StepResult:
        outline_by_location = {
            (chapter["chapter_no"], section["section_no"]): section
            for chapter in context.shared_data["scenario_outline"]["chapters"]
            for section in chapter["sections"]
        }
        section_response = _scenario_section_response_schema()
        response_schema = {
            "type": "object",
            "required": ["review_report", "scenario_sections"],
            "additionalProperties": False,
            "properties": {
                "review_report": _review_report_schema(),
                "scenario_sections": section_response["properties"]["scenario_sections"],
            },
        }
        reviewed: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        prompts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        for section in context.shared_data["scenario_sections"]:
            location = (section["chapter_no"], section["section_no"])
            outline_section = outline_by_location[location]
            previous_reviewed_section = reviewed[-1] if reviewed else None
            prompt = (
                "Review and repair one completed scenario section. Return exactly one revised section. "
                "The report must assess the revised section, and passed may be true only when no "
                "critical issue remains.\n\n"
                f"SCENARIO IDEA\n{json.dumps(context.shared_data['input']['scenario_idea'], ensure_ascii=False, indent=2)}\n\n"
                f"CHARACTER PROFILES\n{json.dumps(context.shared_data['character_profiles'], ensure_ascii=False, indent=2)}\n\n"
                f"SECTION OUTLINE\n{json.dumps(outline_section, ensure_ascii=False, indent=2)}\n\n"
                f"PREVIOUS REVIEWED SECTION\n{json.dumps(previous_reviewed_section, ensure_ascii=False, indent=2)}\n\n"
                f"GENERATED SECTION\n{json.dumps(section, ensure_ascii=False, indent=2)}\n\n"
                "Check chronology and location continuity, speaker attribution, character voice, planning "
                "alignment, unauthorized events or characters, repeated actions and slogans, actual scene "
                "progress, internal instruction leakage, and domain plausibility. Correct every finding while "
                "checking that every SECTION OUTLINE planned_state_updates commitment becomes observable "
                "in the prose and durable changes are represented by state_updates. "
                "preserving chapter_no, section_no, section_title, required completed_event_ids, and globally "
                "unique block IDs. Preserve all durable state_updates facts unless the repair explicitly makes "
                "one invalid. Continue from PREVIOUS REVIEWED SECTION without resetting its outcome. Only "
                "report this section's SECTION OUTLINE event IDs in completed_event_ids; never copy completed "
                "event IDs from PREVIOUS REVIEWED SECTION. "
                "character IDs in the profiles may speak; render incidental NPC speech as "
                "narration unless that NPC has a profile. Do not merely describe the repair."
            )
            reviewed_section, report, metrics = self._review_section_unit(
                context=context,
                prompt=prompt,
                response_schema=response_schema,
                location=location,
                original_section=section,
                outline_section=outline_section,
                previous_reviewed_section=previous_reviewed_section,
            )
            reviewed.append(reviewed_section)
            reports.append(
                {"chapter_no": location[0], "section_no": location[1], "report": report}
            )
            prompts.append(prompt)
            input_tokens += metrics["input_tokens"]
            output_tokens += metrics["output_tokens"]
        combined_prompt = "\n\n--- NEXT SECTION REVIEW ---\n\n".join(prompts)
        return StepResult(
            output={"scenario_sections": reviewed, "section_review_reports": reports},
            prompt=combined_prompt,
            prompt_version="v1",
            prompt_hash=hashlib.sha256(combined_prompt.encode("utf-8")).hexdigest(),
            model=context.config.text_generation.model,
            temperature=context.config.temperature_for(self.name),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _review_section_unit(
        self,
        *,
        context: StepContext,
        prompt: str,
        response_schema: dict[str, Any],
        location: tuple[int, int],
        original_section: dict[str, Any],
        outline_section: dict[str, Any],
        previous_reviewed_section: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
        unit_key = f"chapter-{location[0]:03d}-section-{location[1]:03d}"
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "prompt": prompt,
                    "model": context.config.text_generation.model,
                    "schema": response_schema,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        checkpoint_dir = Path(context.artifacts_dir) / "section-review-units"
        checkpoint_path = checkpoint_dir / f"{unit_key}.json"

        if checkpoint_path.exists() and not context.force:
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if checkpoint.get("fingerprint") == fingerprint:
                    reviewed, report = self._validate_reviewed_section(
                        checkpoint["response"],
                        context=context,
                        location=location,
                        original_section=original_section,
                        outline_section=outline_section,
                        previous_reviewed_section=previous_reviewed_section,
                    )
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": self.name,
                            "event": "section_review_unit_checkpoint_loaded",
                            "unit": unit_key,
                        }
                    )
                    return reviewed, report, {"input_tokens": 0, "output_tokens": 0}
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": self.name,
                        "event": "section_review_unit_checkpoint_rejected",
                        "unit": unit_key,
                        "failure_reason": str(exc),
                    }
                )

        strategy = context.config.retry_strategy
        phases = ["initial"]
        phases.extend("short_retry" for _ in range(strategy.short_retries))
        phases.extend(
            "prompt_revision" for _ in range(strategy.prompt_revision_retries)
        )
        if strategy.fallback_enabled:
            phases.append("fallback")
        failure_reason = ""
        input_tokens = 0
        output_tokens = 0
        for attempt, phase in enumerate(phases, start=1):
            attempt_prompt = prompt
            if failure_reason:
                attempt_prompt += (
                    "\n\nPREVIOUS ATTEMPT VALIDATION FAILURE\n"
                    f"{failure_reason}\n\nCorrect this exact failure without shortening "
                    "the section below its accepted range. Return the complete section."
                )
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": self.name,
                    "event": "section_review_unit_started",
                    "unit": unit_key,
                    "unit_attempt": attempt,
                    "retry_phase": phase,
                    "previous_failure_reason": failure_reason or None,
                }
            )
            try:
                response = context.text_generation_provider.generate_json(
                    prompt=attempt_prompt,
                    model=context.config.text_generation.model,
                    temperature=context.config.temperature_for(self.name),
                    response_schema=response_schema,
                    response_name=f"scenario_section_review_{location[0]}_{location[1]}",
                )
                input_tokens += response.input_tokens or 0
                output_tokens += response.output_tokens or 0
                reviewed, report = self._validate_reviewed_section(
                    response.data,
                    context=context,
                    location=location,
                    original_section=original_section,
                    outline_section=outline_section,
                    previous_reviewed_section=previous_reviewed_section,
                )
            except Exception as exc:  # noqa: BLE001
                if is_non_retryable_provider_error(exc):
                    raise
                failure_reason = str(exc)
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": self.name,
                        "event": "section_review_unit_failed",
                        "unit": unit_key,
                        "unit_attempt": attempt,
                        "retry_phase": phase,
                        "failure_reason": failure_reason,
                    }
                )
                continue

            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            temporary_path = checkpoint_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "response": {
                            "review_report": report,
                            "scenario_sections": [reviewed],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary_path.replace(checkpoint_path)
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": self.name,
                    "event": "section_review_unit_succeeded",
                    "unit": unit_key,
                    "unit_attempt": attempt,
                    "retry_phase": phase,
                }
            )
            return reviewed, report, {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }

        raise RuntimeError(
            f"Section review unit {unit_key} exhausted {len(phases)} attempts. "
            f"Last error: {failure_reason}"
        )

    def _validate_reviewed_section(
        self,
        data: dict[str, Any],
        *,
        context: StepContext,
        location: tuple[int, int],
        original_section: dict[str, Any],
        outline_section: dict[str, Any],
        previous_reviewed_section: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        report = data["review_report"]
        if report["repair_scope"] == "outline" or not report["passed"]:
            problems = "; ".join(item["problem"] for item in report["findings"])
            raise ValueError(
                f"Reviewed section has unresolved issues at chapter {location[0]} "
                f"section {location[1]}: {problems}"
            )
        sections = data["scenario_sections"]
        if len(sections) != 1:
            raise ValueError("Section review must return exactly one scenario section")
        reviewed = self._restore_section_contract(
            sections[0],
            original_section=original_section,
            outline_section=outline_section,
        )
        previous_block_ids = {
            block["block_id"]
            for block in (previous_reviewed_section or {}).get("narrative_blocks", [])
        }
        repeated = previous_block_ids.intersection(
            block["block_id"] for block in reviewed["narrative_blocks"]
        )
        if repeated:
            raise ConsistencyCheckError(
                f"reviewed section repeats previous block IDs: {sorted(repeated)}"
            )
        body = context.config.scenario_body_generation
        subsection_count = body.subsections_per_section
        ScenarioBodyQualityChecker().check_section(
            generated_section=reviewed,
            outline_section=outline_section,
            valid_character_ids={
                profile["character_id"]
                for profile in context.shared_data["character_profiles"]
            },
            min_characters=body.min_characters * subsection_count,
            max_characters=body.max_characters * subsection_count,
            min_dialogue_blocks=body.min_dialogue_blocks * subsection_count,
            max_dialogue_blocks=body.max_dialogue_blocks * subsection_count,
            require_event_mentions=body.require_event_mentions,
        )
        return reviewed, report

    @staticmethod
    def _restore_section_contract(
        reviewed_section: dict[str, Any],
        *,
        original_section: dict[str, Any],
        outline_section: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep reviewer output bound to the section's immutable plan contract."""
        restored = deepcopy(reviewed_section)
        restored["chapter_no"] = original_section["chapter_no"]
        restored["section_no"] = original_section["section_no"]
        restored["section_title"] = original_section["section_title"]
        restored["state_updates"]["completed_event_ids"] = [
            event["event_id"] for event in outline_section["key_events"]
        ]
        return restored

    def requires_review_after_success(self, context: StepContext) -> bool:
        return context.config.scenario_review.require_human_review
