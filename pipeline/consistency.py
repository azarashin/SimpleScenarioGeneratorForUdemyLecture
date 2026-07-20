from __future__ import annotations

import binascii
import re
import struct
import unicodedata
import zlib
from pathlib import Path
from typing import Any

from .errors import ConsistencyCheckError
from .scenario_quality import ScenarioBodyQualityChecker


CANONICAL_EXPRESSIONS = (
    "neutral",
    "smile",
    "serious",
    "thinking",
    "surprised",
    "worried",
    "confused",
    "angry",
    "sad",
    "relieved",
    "embarrassed",
    "nervous",
    "confident",
    "doubtful",
    "shocked",
    "determined",
)


class PipelineConsistencyChecker:
    def __init__(self) -> None:
        self.scenario_quality_checker = ScenarioBodyQualityChecker()

    def check(
        self,
        shared_data: dict[str, Any],
        output: dict[str, Any],
        *,
        run_root: Path | None = None,
    ) -> None:
        candidate = {**shared_data, **output}
        if "character_profiles" in output:
            self._check_character_profiles(candidate)
        if "scenario_outline" in output:
            self._check_outline(candidate)
        if "scenario_sections" in output:
            self._check_sections(candidate)
        if "dialogue_expression_tags" in output:
            self._check_dialogue_expression_tags(candidate)
        if "character_image_assets" in output:
            self._check_character_image_assets(candidate, run_root=run_root)

    def _check_character_image_assets(
        self,
        data: dict[str, Any],
        *,
        run_root: Path | None,
    ) -> None:
        profiles = self._unique_by_id(data["character_profiles"], "character_profiles")
        assets = self._unique_by_id(
            data["character_image_assets"], "character_image_assets"
        )
        if set(assets) != set(profiles):
            self._fail("character IDs differ between profiles and image assets")

        image_config = data.get("_image_generation_config", {})
        expected_width = image_config.get("width")
        expected_height = image_config.get("height")
        expression_width = image_config.get("expression_width", expected_width)
        expression_height = image_config.get("expression_height", expected_height)
        for character_id, profile in profiles.items():
            asset = assets[character_id]
            available = set(
                profile["emotion_model"]["available_expressions"]
            )
            expressions = set(asset["expression_images"])
            missing = available - expressions
            if missing:
                self._fail(
                    f"character {character_id} image assets are missing expressions: "
                    f"{sorted(missing)}"
                )
            unknown = expressions - available
            if unknown:
                self._fail(
                    f"character {character_id} image assets contain unknown expressions: "
                    f"{sorted(unknown)}"
                )

            paths = {
                "base": asset["base_image_path"],
                **asset["expression_images"],
            }
            for label, relative_path in paths.items():
                is_base = label == "base"
                self._check_image_file(
                    character_id=character_id,
                    label=label,
                    relative_path=relative_path,
                    run_root=run_root,
                    expected_width=(expected_width if is_base else expression_width),
                    expected_height=(
                        expected_height if is_base else expression_height
                    ),
                )

    def _check_image_file(
        self,
        *,
        character_id: str,
        label: str,
        relative_path: str,
        run_root: Path | None,
        expected_width: int | None,
        expected_height: int | None,
    ) -> None:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            self._fail(
                f"character {character_id} {label} image path must be a safe relative path"
            )
        if run_root is None:
            self._fail("run root is required to validate character image files")
        resolved_root = run_root.resolve()
        resolved_path = (resolved_root / path).resolve()
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError:
            self._fail(
                f"character {character_id} {label} image path escapes the run directory"
            )
        if not resolved_path.is_file():
            self._fail(
                f"character {character_id} {label} image file does not exist: "
                f"{relative_path}"
            )
        image_bytes = resolved_path.read_bytes()
        if not image_bytes:
            self._fail(
                f"character {character_id} {label} image file is empty: {relative_path}"
            )
        try:
            image_format, width, height = self._image_info(image_bytes)
        except ValueError as exc:
            self._fail(
                f"character {character_id} {label} is not a supported image: {exc}"
            )
        expected_format = {
            ".png": "png",
            ".jpg": "jpeg",
            ".jpeg": "jpeg",
            ".webp": "webp",
        }.get(resolved_path.suffix.casefold())
        if expected_format != image_format:
            self._fail(
                f"character {character_id} {label} image extension does not match "
                f"its {image_format} content"
            )
        if expected_width is not None and width != expected_width:
            self._fail(
                f"character {character_id} {label} image width differs from config: "
                f"{width} != {expected_width}"
            )
        if expected_height is not None and height != expected_height:
            self._fail(
                f"character {character_id} {label} image height differs from config: "
                f"{height} != {expected_height}"
            )

    @staticmethod
    def _image_info(image_bytes: bytes) -> tuple[str, int, int]:
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return ("png", *PipelineConsistencyChecker._png_dimensions(image_bytes))
        if image_bytes.startswith(b"\xff\xd8"):
            if not image_bytes.endswith(b"\xff\xd9"):
                raise ValueError("JPEG end marker is missing")
            return ("jpeg", *PipelineConsistencyChecker._jpeg_dimensions(image_bytes))
        if (
            len(image_bytes) >= 30
            and image_bytes[:4] == b"RIFF"
            and image_bytes[8:12] == b"WEBP"
        ):
            declared_size = int.from_bytes(image_bytes[4:8], "little") + 8
            if declared_size != len(image_bytes):
                raise ValueError("WebP RIFF size does not match file size")
            return ("webp", *PipelineConsistencyChecker._webp_dimensions(image_bytes))
        raise ValueError("expected PNG, JPEG, or WebP content")

    @staticmethod
    def _png_dimensions(image_bytes: bytes) -> tuple[int, int]:
        index = 8
        width = height = None
        saw_end = False
        compressed_pixels = bytearray()
        while index + 12 <= len(image_bytes):
            length = int.from_bytes(image_bytes[index : index + 4], "big")
            chunk_end = index + 12 + length
            if chunk_end > len(image_bytes):
                raise ValueError("PNG chunk is truncated")
            chunk_type = image_bytes[index + 4 : index + 8]
            chunk_data = image_bytes[index + 8 : index + 8 + length]
            expected_crc = int.from_bytes(
                image_bytes[index + 8 + length : chunk_end], "big"
            )
            actual_crc = binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                raise ValueError("PNG chunk checksum is invalid")
            if index == 8:
                if chunk_type != b"IHDR" or length != 13:
                    raise ValueError("invalid PNG header")
                width, height = struct.unpack(">II", chunk_data[:8])
            if chunk_type == b"IDAT":
                compressed_pixels.extend(chunk_data)
            if chunk_type == b"IEND":
                saw_end = True
                if chunk_end != len(image_bytes):
                    raise ValueError("PNG contains data after its end marker")
                break
            index = chunk_end
        if width is None or height is None or not saw_end:
            raise ValueError("PNG is missing required chunks")
        if not compressed_pixels:
            raise ValueError("PNG contains no image data")
        try:
            zlib.decompress(compressed_pixels)
        except zlib.error as exc:
            raise ValueError("PNG image data cannot be decompressed") from exc
        if width <= 0 or height <= 0:
            raise ValueError("PNG dimensions must be greater than zero")
        return width, height

    @staticmethod
    def _jpeg_dimensions(image_bytes: bytes) -> tuple[int, int]:
        index = 2
        while index + 9 < len(image_bytes):
            if image_bytes[index] != 0xFF:
                index += 1
                continue
            marker = image_bytes[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(image_bytes):
                break
            segment_length = int.from_bytes(image_bytes[index : index + 2], "big")
            if segment_length < 2 or index + segment_length > len(image_bytes):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if segment_length < 7:
                    break
                height = int.from_bytes(image_bytes[index + 3 : index + 5], "big")
                width = int.from_bytes(image_bytes[index + 5 : index + 7], "big")
                return width, height
            index += segment_length
        raise ValueError("JPEG dimensions could not be read")

    @staticmethod
    def _webp_dimensions(image_bytes: bytes) -> tuple[int, int]:
        chunk = image_bytes[12:16]
        if chunk == b"VP8X" and len(image_bytes) >= 30:
            width = 1 + int.from_bytes(image_bytes[24:27], "little")
            height = 1 + int.from_bytes(image_bytes[27:30], "little")
            return width, height
        if chunk == b"VP8L" and len(image_bytes) >= 25 and image_bytes[20] == 0x2F:
            bits = int.from_bytes(image_bytes[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8 " and len(image_bytes) >= 30:
            frame = image_bytes.find(b"\x9d\x01\x2a", 20)
            if frame >= 0 and frame + 7 <= len(image_bytes):
                width = int.from_bytes(image_bytes[frame + 3 : frame + 5], "little")
                height = int.from_bytes(image_bytes[frame + 5 : frame + 7], "little")
                return width & 0x3FFF, height & 0x3FFF
        raise ValueError("WebP dimensions could not be read")

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
            relationships = profile.get("relationships", [])
            unknown_relationship_ids = {
                relationship["target_character_id"]
                for relationship in relationships
                if relationship["target_character_id"] not in profile_by_id
            }
            if unknown_relationship_ids:
                self._fail(
                    f"character {character_id} relationships reference unknown "
                    f"characters: {sorted(unknown_relationship_ids)}"
                )
            available_expressions = set(
                profile["emotion_model"]["available_expressions"]
            )
            if profile["emotion_model"]["available_expressions"] != list(
                CANONICAL_EXPRESSIONS
            ):
                self._fail(
                    f"character {character_id} available expressions must equal "
                    "the canonical expression list in canonical order"
                )
            unknown_preferred_expressions = set(
                profile["emotion_model"].get("preferred_expressions", [])
            ) - available_expressions
            if unknown_preferred_expressions:
                self._fail(
                    f"character {character_id} prefers unavailable expressions: "
                    f"{sorted(unknown_preferred_expressions)}"
                )
            preferred_expressions = profile["emotion_model"].get(
                "preferred_expressions", []
            )
            if preferred_expressions and "neutral" not in preferred_expressions:
                self._fail(
                    f"character {character_id} preferred expressions must include neutral"
                )
            unknown_rule_expressions = {
                rule["expression"]
                for rule in profile["emotion_model"].get("expression_rules", [])
                if rule["expression"] not in available_expressions
            }
            if unknown_rule_expressions:
                self._fail(
                    f"character {character_id} expression rules reference unavailable "
                    f"expressions: {sorted(unknown_rule_expressions)}"
                )

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
        first_section_participants: set[str] | None = None
        for chapter in chapters:
            sections = chapter["sections"]
            self._require_sequence(
                [section["section_no"] for section in sections],
                target["sections_per_chapter"],
                f"chapter {chapter['chapter_no']} section timeline",
            )
            for section in sections:
                participant_list = section["participating_characters"]
                if len(participant_list) != len(set(participant_list)):
                    self._fail(
                        f"chapter {chapter['chapter_no']} section "
                        f"{section['section_no']} contains duplicate participating characters"
                    )
                if first_section_participants is None:
                    first_section_participants = set(participant_list)
                unknown = set(section["participating_characters"]) - valid_characters
                if unknown:
                    self._fail(
                        f"chapter {chapter['chapter_no']} section {section['section_no']} "
                        f"references unknown characters: {sorted(unknown)}"
                    )
                section_event_ids = [
                    event["event_id"] for event in section["key_events"]
                ]
                if len(section_event_ids) != len(set(section_event_ids)):
                    self._fail(
                        f"chapter {chapter['chapter_no']} section "
                        f"{section['section_no']} contains repeated key events"
                    )
                subsections = section.get("subsections", [])
                if subsections:
                    self._require_sequence(
                        [item["subsection_no"] for item in subsections],
                        len(subsections),
                        (
                            f"chapter {chapter['chapter_no']} section "
                            f"{section['section_no']} subsection timeline"
                        ),
                    )
                    all_subsection_event_ids = [
                        event["event_id"]
                        for item in subsections
                        for event in item["key_events"]
                    ]
                    if len(all_subsection_event_ids) != len(
                        set(all_subsection_event_ids)
                    ):
                        self._fail(
                            f"chapter {chapter['chapter_no']} section "
                            f"{section['section_no']} recycles events across subsections"
                        )
                    if set(all_subsection_event_ids) != set(section_event_ids):
                        self._fail(
                            f"chapter {chapter['chapter_no']} section "
                            f"{section['section_no']} subsection events differ from "
                            "section events"
                        )
                    for subsection in subsections:
                        if subsection["state_change"] not in subsection["key_events"]:
                            self._fail(
                                f"chapter {chapter['chapter_no']} section "
                                f"{section['section_no']} subsection "
                                f"{subsection['subsection_no']} state_change must be one "
                                "of its key events"
                            )
        if (
            len(valid_characters) >= 5
            and first_section_participants == valid_characters
        ):
            self._fail(
                "the first section must not introduce the entire cast when five or more "
                "characters exist"
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
            body_config = data.get("_scenario_body_generation_config")
            if body_config:
                self.scenario_quality_checker.check_section(
                    generated_section=section,
                    outline_section=outline_section,
                    valid_character_ids=valid_characters,
                    min_characters=body_config["min_characters"],
                    max_characters=body_config["max_characters"],
                    min_dialogue_blocks=body_config.get("min_dialogue_blocks", 1),
                    max_dialogue_blocks=body_config.get("max_dialogue_blocks", 2**31 - 1),
                    require_event_mentions=body_config["require_event_mentions"],
                )

    def _check_dialogue_expression_tags(self, data: dict[str, Any]) -> None:
        profiles = self._unique_by_id(data["character_profiles"], "character_profiles")
        dialogue_blocks = [
            (section["chapter_no"], section["section_no"], block)
            for section in data["scenario_sections"]
            for block in section["narrative_blocks"]
            if block["type"] == "dialogue"
        ]
        tags = data["dialogue_expression_tags"]
        expected_locations = [
            (chapter_no, section_no, block["block_id"], block["speaker_id"])
            for chapter_no, section_no, block in dialogue_blocks
        ]
        actual_locations = [
            (
                tag["chapter_no"],
                tag["section_no"],
                tag["block_id"],
                tag["speaker_id"],
            )
            for tag in tags
        ]
        if actual_locations != expected_locations:
            self._fail(
                "dialogue expression tags must cover every dialogue block in display order"
            )
        for tag in tags:
            available = profiles[tag["speaker_id"]]["emotion_model"][
                "available_expressions"
            ]
            if tag["expression"] not in available:
                self._fail(
                    f"character {tag['speaker_id']} does not support expression "
                    f"{tag['expression']!r}"
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
