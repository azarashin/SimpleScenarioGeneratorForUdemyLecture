from __future__ import annotations

import unicodedata
from typing import Any

from .errors import ConsistencyCheckError


class ScenarioBodyQualityChecker:
    def check_section(
        self,
        *,
        generated_section: dict[str, Any],
        outline_section: dict[str, Any],
        valid_character_ids: set[str],
        min_characters: int,
        max_characters: int,
        require_event_mentions: bool,
    ) -> None:
        location = (
            f"chapter {generated_section['chapter_no']} "
            f"section {generated_section['section_no']}"
        )
        participants = set(outline_section["participating_characters"])
        narration_count = 0
        dialogue_count = 0
        text_parts: list[str] = []

        for block in generated_section["narrative_blocks"]:
            text_parts.append(block["text"])
            narration_count += int(block["type"] == "narration")
            dialogue_count += int(block["type"] == "dialogue")
            speaker_id = block["speaker_id"]
            if speaker_id is not None and speaker_id not in valid_character_ids:
                self._fail(f"unknown speaker {speaker_id!r} at {location}")
            if speaker_id is not None and speaker_id not in participants:
                self._fail(f"speaker {speaker_id!r} is not a participant at {location}")

        if narration_count == 0 or dialogue_count == 0:
            self._fail(f"{location} must contain narration and dialogue")

        combined_text = "".join(text_parts)
        character_count = sum(not character.isspace() for character in combined_text)
        if not min_characters <= character_count <= max_characters:
            self._fail(
                f"{location} body length must be {min_characters}-{max_characters} "
                f"non-whitespace characters, got {character_count}"
            )

        if require_event_mentions:
            normalized_text = unicodedata.normalize("NFKC", combined_text).casefold()
            missing_events = [
                event
                for event in outline_section["key_events"]
                if unicodedata.normalize("NFKC", event).casefold() not in normalized_text
            ]
            if missing_events:
                self._fail(f"{location} does not cover required events: {missing_events}")

    @staticmethod
    def _fail(reason: str) -> None:
        raise ConsistencyCheckError(f"Consistency check failed: {reason}")
