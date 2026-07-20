from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .text_generation import _scenario_section_response_schema
from .types import Step, StepContext, StepResult


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
        response_schema = {
            "type": "object",
            "required": ["review_report", "scenario_outline"],
            "additionalProperties": False,
            "properties": {
                "review_report": _review_report_schema(),
                "scenario_outline": _inline_common_refs(outline_schema, schemas_dir),
            },
        }
        prompt = (
            "Review and REWRITE this complete scenario outline before prose generation.\n"
            "This is a repair step, not a rejection-only audit. Generic placeholders, empty "
            "planning ledgers, repeated state descriptions, and missing concrete story details "
            "are defects you MUST repair in the returned scenario_outline. They are not reasons "
            "to return the input unchanged or set passed=false. Return passed=true after making "
            "all repairs that can be derived from PLANNING INPUT and CHARACTER PROFILES. Set "
            "passed=false only for a genuine contradiction in those authoritative inputs that "
            "cannot be resolved without a human decision. The report must assess the REVISED "
            "outline, never the submitted draft.\n\n"
            f"PLANNING INPUT\n{json.dumps(context.shared_data['input'], ensure_ascii=False, indent=2)}\n\n"
            f"CHARACTER PROFILES\n{json.dumps(context.shared_data['character_profiles'], ensure_ascii=False, indent=2)}\n\n"
            f"OUTLINE\n{json.dumps(context.shared_data['scenario_outline'], ensure_ascii=False, indent=2)}\n\n"
            "Check story order, must_include and must_avoid constraints, chapter/section purpose, "
            "character introduction timing, repeated beats, and concrete causally connected events. "
            "Replace generic event descriptions with specific story events. Preserve title exactly as "
            "PLANNING INPUT.scenario_idea.title and preserve logline exactly as "
            "PLANNING INPUT.scenario_idea.premise; do not paraphrase either value. Preserve every event_id, "
            "chapter/section/subsection number, configured counts, and valid character IDs. Each "
            "subsection must have a distinct start state, irreversible change, end state, and handoff. "
            "Do not invent an extra central case when the planning input already assigns cases to chapters. "
            "Build story_plan before approving the outline: inventory plot threads, foreshadowing, and "
            "character arcs with valid event IDs and chronological plant/open/turn/payoff/resolve points. "
            "Populate each subsection's planned_state_updates with concrete state changes. Every ledger "
            "opening, resolution, planting, payoff, and character turning point must appear in the matching "
            "subsection. Ensure each resulting_state_summary can serve as the next subsection's starting "
            "condition without replaying the previous scene. Replace every English planning placeholder "
            "such as 'Establish the changed situation' with a story-specific event written in the same "
            "language as the planning input. Do not report a placeholder as a finding unless it remains "
            "in the revised output; remove it by rewriting the outline instead."
        )
        response = context.text_generation_provider.generate_json(
            prompt=prompt,
            model=context.config.text_generation.model,
            temperature=context.config.temperature_for(self.name),
            response_schema=response_schema,
            response_name="scenario_outline_review",
        )
        report = response.data["review_report"]
        if not report["passed"]:
            problems = "; ".join(item["problem"] for item in report["findings"])
            raise ValueError(f"Reviewed outline still has unresolved issues: {problems}")
        reviewed_outline = self._restore_immutable_fields(
            response.data["scenario_outline"],
            context.shared_data["input"]["scenario_idea"],
        )
        return StepResult(
            output={
                "scenario_outline": reviewed_outline,
                "outline_review_report": report,
            },
            prompt=prompt,
            prompt_version="v1",
            prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            model=response.model,
            temperature=context.config.temperature_for(self.name),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

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
                "character IDs in the profiles may speak; render incidental NPC speech as "
                "narration unless that NPC has a profile. Do not merely describe the repair."
            )
            response = context.text_generation_provider.generate_json(
                prompt=prompt,
                model=context.config.text_generation.model,
                temperature=context.config.temperature_for(self.name),
                response_schema=response_schema,
                response_name=f"scenario_section_review_{location[0]}_{location[1]}",
            )
            report = response.data["review_report"]
            if report["repair_scope"] == "outline":
                problems = "; ".join(item["problem"] for item in report["findings"])
                raise ValueError(
                    f"Section review requires outline repair at chapter {location[0]} "
                    f"section {location[1]}: {problems}"
                )
            if not report["passed"]:
                problems = "; ".join(item["problem"] for item in report["findings"])
                raise ValueError(
                    f"Reviewed section still has unresolved issues at chapter "
                    f"{location[0]} section {location[1]}: {problems}"
                )
            sections = response.data["scenario_sections"]
            if len(sections) != 1:
                raise ValueError("Section review must return exactly one scenario section")
            reviewed.append(sections[0])
            reports.append(
                {"chapter_no": location[0], "section_no": location[1], "report": report}
            )
            prompts.append(prompt)
            input_tokens += response.input_tokens or 0
            output_tokens += response.output_tokens or 0
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

    def requires_review_after_success(self, context: StepContext) -> bool:
        return context.config.scenario_review.require_human_review
