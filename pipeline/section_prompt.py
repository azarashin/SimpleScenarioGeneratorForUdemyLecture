from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from .prompts import PromptCatalog


@dataclass(frozen=True, slots=True)
class RenderedSectionPrompt:
    text: str
    version: str
    template_hash: str
    rendered_hash: str


class ScenarioSectionPromptBuilder:
    step_name = "step-04-generate-sections"

    def __init__(
        self,
        *,
        catalog: PromptCatalog | None = None,
        schemas_dir: Path | None = None,
    ) -> None:
        self.catalog = catalog or PromptCatalog()
        self.schemas_dir = schemas_dir or Path(__file__).resolve().parent.parent / "schemas"

    def build(
        self,
        *,
        scenario_idea: dict[str, Any],
        character_profiles: list[dict[str, Any]],
        chapter: dict[str, Any],
        section: dict[str, Any],
        subsection: dict[str, Any],
        previous_state: dict[str, Any],
        target_characters: int = 1200,
        min_characters: int = 1000,
        max_characters: int = 1600,
        min_dialogue_blocks: int = 6,
        max_dialogue_blocks: int = 14,
        version: str | None = None,
    ) -> RenderedSectionPrompt:
        definition = self.catalog.resolve(self.step_name, version)
        allowed_ids = list(section["participating_characters"])
        profiles_by_id = {
            profile["character_id"]: profile for profile in character_profiles
        }
        known_ids = set(profiles_by_id)
        unknown_ids = set(allowed_ids) - known_ids
        if unknown_ids:
            raise ValueError(
                f"Cannot build section prompt with unknown character IDs: {sorted(unknown_ids)}"
            )
        participating_profiles = [profiles_by_id[item] for item in allowed_ids]

        variables = {
            "scenario_idea_json": self._json(scenario_idea),
            "character_profiles_json": self._json(participating_profiles),
            "chapter_json": self._json(
                {
                    "chapter_no": chapter["chapter_no"],
                    "chapter_title": chapter["chapter_title"],
                    "chapter_goal": chapter["chapter_goal"],
                }
            ),
            "section_json": self._json(section),
            "subsection_json": self._json(subsection),
            "previous_state_json": self._json(previous_state),
            "allowed_character_ids_json": self._json(allowed_ids),
            "output_schema_json": self._json(self._schema_bundle()),
            "target_characters": str(target_characters),
            "min_characters": str(min_characters),
            "max_characters": str(max_characters),
            "min_dialogue_blocks": str(min_dialogue_blocks),
            "max_dialogue_blocks": str(max_dialogue_blocks),
        }
        template_text = definition.text.replace(
            "\n\nPREVIOUS SECTION STATE OR SUMMARY",
            "\n\nTARGET SUBSECTION\n${subsection_json}"
            "\n\nPREVIOUS SECTION STATE OR SUMMARY",
        )
        template_text = template_text.replace(
            "Continue from PREVIOUS SECTION STATE OR SUMMARY without contradicting "
            "character_locations, possessions, known_information, relationship_changes, "
            "occurred_events, or unresolved_plot_threads. The complete previous_section "
            "is authoritative context.",
            "Continue from PREVIOUS SECTION STATE OR SUMMARY without contradicting "
            "character_locations, possessions, known_information, relationship_changes, "
            "introduced_entities, occurred_events, unresolved_plot_threads, or "
            "recent_context. This compact cumulative state is authoritative context.\n"
            "- Populate state_updates with every durable fact introduced or changed by "
            "this section. Use character_locations and possessions only for characters "
            "changed here; known_information for durable discoveries; "
            "relationship_changes for durable relationship changes; introduced_entities "
            "for newly established people, places, organizations, objects, or concepts; "
            "unresolved_plot_threads for newly opened questions; resolved_plot_threads "
            "for existing thread strings resolved here; and continuity_summary for a "
            "concise account of the resulting situation needed by the next section. Use "
            "empty arrays or objects when there are no updates.",
        )
        try:
            text = Template(template_text).substitute(variables)
        except KeyError as exc:
            raise ValueError(
                f"Missing prompt template variable in {self.step_name} "
                f"{definition.version}: {exc.args[0]}"
            ) from exc
        rendered_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RenderedSectionPrompt(
            text,
            definition.version,
            definition.content_hash,
            rendered_hash,
        )

    def _schema_bundle(self) -> dict[str, Any]:
        sections_schema = self._read_schema("scenario-sections.schema.json")
        return {
            "output": {
                "type": "object",
                "required": ["scenario_sections"],
                "additionalProperties": False,
                "properties": {
                    "scenario_sections": {
                        **sections_schema,
                        "minItems": 1,
                        "maxItems": 1,
                    }
                },
            },
            "common": self._read_schema("common.schema.json"),
        }

    def _read_schema(self, name: str) -> dict[str, Any]:
        return json.loads((self.schemas_dir / name).read_text(encoding="utf-8"))

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
