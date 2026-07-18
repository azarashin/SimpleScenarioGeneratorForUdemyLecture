from __future__ import annotations

import re
import unicodedata
from typing import Any


class ConsistencyCheckError(ValueError):
    """Raised when generated data contradicts previously established data."""


class PipelineConsistencyChecker:
    def check(self, shared_data: dict[str, Any], output: dict[str, Any]) -> None:
        candidate = {**shared_data, **output}
        if "character_profiles" in output:
            self._check_character_profiles(candidate)
        if "scenario_outline" in output:
            self._check_outline(candidate)
        if "scenario_sections" in output:
            self._check_sections(candidate)

    def _check_character_profiles(self, data: dict[str, Any]) -> None:
        pipeline_input = self._pipeline_input(data)
        overviews = pipeline_input.get("character_overviews", [])
        profiles = data["character_profiles"]
        overview_by_id = self._unique_by_id(overviews, "character_overviews")
        profile_by_id = self._unique_by_id(profiles, "character_profiles")

        if set(profile_by_id) != set(overview_by_id):
            self._fail("character IDs differ between overviews and profiles")

        names_by_normalized: dict[str, str] = {}
        for character_id, profile in profile_by_id.items():
            overview = overview_by_id[character_id]
            for field in ("name", "role"):
                if profile[field] != overview[field]:
                    self._fail(
                        f"character {character_id} has inconsistent {field}: "
                        f"{overview[field]!r} != {profile[field]!r}"
                    )
            normalized_name = self._normalize_name(profile["name"])
            existing_id = names_by_normalized.get(normalized_name)
            if existing_id and existing_id != character_id:
                self._fail(
                    f"ambiguous character naming: {existing_id} and {character_id} "
                    f"normalize to {normalized_name!r}"
                )
            names_by_normalized[normalized_name] = character_id

    def _check_outline(self, data: dict[str, Any]) -> None:
        pipeline_input = self._pipeline_input(data)
        idea = pipeline_input["scenario_idea"]
        outline = data["scenario_outline"]
        if outline["title"] != idea["title"]:
            self._fail("scenario title changed between input and outline")
        if outline["logline"] != idea["premise"]:
            self._fail("scenario premise changed between input and outline")

        target = idea["target_length"]
        chapters = outline["chapters"]
        self._require_sequence(
            [chapter["chapter_no"] for chapter in chapters],
            target["chapter_count"],
            "chapter timeline",
        )
        valid_characters = self._character_ids(data)
        for chapter in chapters:
            sections = chapter["sections"]
            self._require_sequence(
                [section["section_no"] for section in sections],
                target["sections_per_chapter"],
                f"chapter {chapter['chapter_no']} section timeline",
            )
            for section in sections:
                unknown = set(section["participating_characters"]) - valid_characters
                if unknown:
                    self._fail(
                        f"chapter {chapter['chapter_no']} section {section['section_no']} "
                        f"references unknown characters: {sorted(unknown)}"
                    )

    def _check_sections(self, data: dict[str, Any]) -> None:
        outline_by_location = {
            (chapter["chapter_no"], section["section_no"]): section
            for chapter in data["scenario_outline"]["chapters"]
            for section in chapter["sections"]
        }
        outline_sections = [
            (chapter["chapter_no"], section["section_no"], section["section_title"])
            for chapter in data["scenario_outline"]["chapters"]
            for section in chapter["sections"]
        ]
        generated_sections = [
            (section["chapter_no"], section["section_no"], section["section_title"])
            for section in data["scenario_sections"]
        ]
        if generated_sections != outline_sections:
            self._fail(
                "section timeline differs from outline (order, numbering, title, or coverage)"
            )

        valid_characters = self._character_ids(data)
        block_ids: set[str] = set()
        for section in data["scenario_sections"]:
            location = f"chapter {section['chapter_no']} section {section['section_no']}"
            outline_section = outline_by_location[
                (section["chapter_no"], section["section_no"])
            ]
            participants = set(outline_section["participating_characters"])
            for block in section["narrative_blocks"]:
                block_id = block["block_id"]
                if block_id in block_ids:
                    self._fail(f"duplicate block_id {block_id!r} at {location}")
                block_ids.add(block_id)
                speaker_id = block["speaker_id"]
                if speaker_id is not None and speaker_id not in valid_characters:
                    self._fail(f"unknown speaker {speaker_id!r} at {location}")
                if speaker_id is not None and speaker_id not in participants:
                    self._fail(
                        f"speaker {speaker_id!r} is not a participant at {location}"
                    )

    @staticmethod
    def _pipeline_input(data: dict[str, Any]) -> dict[str, Any]:
        pipeline_input = data.get("input")
        if not isinstance(pipeline_input, dict):
            raise ConsistencyCheckError("Consistency check failed: pipeline input is missing")
        return pipeline_input

    @staticmethod
    def _unique_by_id(items: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            character_id = item["character_id"]
            if character_id in result:
                raise ConsistencyCheckError(
                    f"Consistency check failed: duplicate character_id "
                    f"{character_id!r} in {source}"
                )
            result[character_id] = item
        return result

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)

    @staticmethod
    def _require_sequence(actual: list[int], count: int, label: str) -> None:
        expected = list(range(1, count + 1))
        if actual != expected:
            raise ConsistencyCheckError(
                f"Consistency check failed: {label} must be {expected}, got {actual}"
            )

    @staticmethod
    def _character_ids(data: dict[str, Any]) -> set[str]:
        return {profile["character_id"] for profile in data["character_profiles"]}

    @staticmethod
    def _fail(reason: str) -> None:
        raise ConsistencyCheckError(f"Consistency check failed: {reason}")
