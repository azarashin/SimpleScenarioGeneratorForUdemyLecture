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
        min_dialogue_blocks: int,
        max_dialogue_blocks: int,
        require_event_mentions: bool,
    ) -> None:
        location = (
            f"chapter {generated_section['chapter_no']} "
            f"section {generated_section['section_no']}"
        )
        participants = set(outline_section["participating_characters"])
        location_updates = {
            item["character_id"]: item["location"]
            for item in generated_section["state_updates"]["character_locations"]
        }
        expected_in_person = {
            item["character_id"]
            for item in outline_section["participant_presence"]
            if item["presence_mode"] == "in_person"
        }
        missing_locations = expected_in_person - set(location_updates)
        if missing_locations:
            self._fail(
                f"{location} does not record scene location for in-person characters: "
                f"{sorted(missing_locations)}"
            )
        wrong_locations = {
            character_id: location_updates[character_id]
            for character_id in expected_in_person
            if location_updates[character_id] != outline_section["scene_location"]
        }
        if wrong_locations:
            self._fail(
                f"{location} places in-person characters outside scene_location "
                f"{outline_section['scene_location']!r}: {wrong_locations}"
            )
        incapacitated = {
            item["character_id"]
            for item in outline_section["participant_presence"]
            if item["participation_status"] == "incapacitated"
        }
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
            if speaker_id in incapacitated:
                self._fail(
                    f"incapacitated character {speaker_id!r} cannot speak at {location}"
                )

        if narration_count == 0 or dialogue_count == 0:
            self._fail(f"{location} must contain narration and dialogue")
        if not min_dialogue_blocks <= dialogue_count <= max_dialogue_blocks:
            self._fail(
                f"{location} dialogue block count must be "
                f"{min_dialogue_blocks}-{max_dialogue_blocks}, got {dialogue_count}"
            )

        combined_text = "".join(text_parts)
        character_count = sum(not character.isspace() for character in combined_text)
        if not min_characters <= character_count <= max_characters:
            self._fail(
                f"{location} body length must be {min_characters}-{max_characters} "
                f"non-whitespace characters, got {character_count}"
            )

        if require_event_mentions:
            normalized_text = unicodedata.normalize("NFKC", combined_text).casefold()
            required_event_ids = {
                event["event_id"] for event in outline_section["key_events"]
            }
            completed_event_ids = set(
                generated_section["state_updates"]["completed_event_ids"]
            )
            missing_event_ids = required_event_ids - completed_event_ids
            if missing_event_ids:
                self._fail(
                    f"{location} does not complete required event IDs: "
                    f"{sorted(missing_event_ids)}"
                )
            unexpected_event_ids = completed_event_ids - required_event_ids
            if unexpected_event_ids:
                self._fail(
                    f"{location} reports unexpected completed event IDs: "
                    f"{sorted(unexpected_event_ids)}"
                )
            leaked_event_ids = {
                event_id
                for event_id in required_event_ids
                if unicodedata.normalize("NFKC", event_id).casefold()
                in normalized_text
            }
            if leaked_event_ids:
                self._fail(
                    f"{location} exposes internal event IDs in narrative text: "
                    f"{sorted(leaked_event_ids)}"
                )

    @staticmethod
    def _fail(reason: str) -> None:
        raise ConsistencyCheckError(f"Consistency check failed: {reason}")
