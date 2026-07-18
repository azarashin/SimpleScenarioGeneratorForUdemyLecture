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
    step_name = "step-03-generate-sections"

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
        previous_state: dict[str, Any],
        version: str | None = None,
    ) -> RenderedSectionPrompt:
        definition = self.catalog.resolve(self.step_name, version)
        allowed_ids = list(section["participating_characters"])
        known_ids = {profile["character_id"] for profile in character_profiles}
        unknown_ids = set(allowed_ids) - known_ids
        if unknown_ids:
            raise ValueError(
                f"Cannot build section prompt with unknown character IDs: {sorted(unknown_ids)}"
            )

        variables = {
            "scenario_idea_json": self._json(scenario_idea),
            "character_profiles_json": self._json(character_profiles),
            "chapter_json": self._json(
                {
                    "chapter_no": chapter["chapter_no"],
                    "chapter_title": chapter["chapter_title"],
                    "chapter_goal": chapter["chapter_goal"],
                }
            ),
            "section_json": self._json(section),
            "previous_state_json": self._json(previous_state),
            "allowed_character_ids_json": self._json(allowed_ids),
            "output_schema_json": self._json(self._schema_bundle()),
        }
        try:
            text = Template(definition.text).substitute(variables)
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
