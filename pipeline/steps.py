from __future__ import annotations

from typing import Any

from .types import Step, StepContext, StepResult


class GenerateCharacterProfilesStep(Step):
    name = "step-01-generate-character-profiles"

    def run(self, context: StepContext) -> StepResult:
        input_data = context.shared_data["input"]
        profiles: list[dict[str, Any]] = []
        for item in input_data["character_overviews"]:
            profiles.append(
                {
                    "character_id": item["character_id"],
                    "name": item["name"],
                    "role": item["role"],
                    "emotion_model": {
                        "available_expressions": ["neutral", "happy", "sad"],
                    },
                }
            )

        return StepResult(
            output={"character_profiles": profiles},
            prompt="Generate character profiles",
            model=context.config.model_name,
            temperature=context.config.temperature,
            input_tokens=120,
            output_tokens=240,
        )


class GenerateOutlineStep(Step):
    name = "step-02-generate-outline"

    def run(self, context: StepContext) -> StepResult:
        input_data = context.shared_data["input"]
        profile_ids = [x["character_id"] for x in context.shared_data["character_profiles"]]
        target = input_data["scenario_idea"]["target_length"]

        chapters = []
        for chapter_no in range(1, target["chapter_count"] + 1):
            sections = []
            for section_no in range(1, target["sections_per_chapter"] + 1):
                sections.append(
                    {
                        "section_no": section_no,
                        "section_title": f"Section {chapter_no}-{section_no}",
                        "section_purpose": "Advance the story",
                        "key_events": ["event-a", "event-b"],
                        "participating_characters": profile_ids,
                    }
                )
            chapters.append(
                {
                    "chapter_no": chapter_no,
                    "chapter_title": f"Chapter {chapter_no}",
                    "chapter_goal": "Progress central conflict",
                    "sections": sections,
                }
            )

        outline = {
            "title": input_data["scenario_idea"]["title"],
            "logline": input_data["scenario_idea"]["premise"],
            "chapters": chapters,
        }

        return StepResult(
            output={"scenario_outline": outline},
            prompt="Generate scenario outline",
            model=context.config.model_name,
            temperature=context.config.temperature,
            input_tokens=180,
            output_tokens=360,
        )


class GenerateSectionsStep(Step):
    name = "step-03-generate-sections"

    def run(self, context: StepContext) -> StepResult:
        outline = context.shared_data["scenario_outline"]
        sections_out: list[dict[str, Any]] = []

        for chapter in outline["chapters"]:
            for section in chapter["sections"]:
                speaker_id = section["participating_characters"][0]
                sections_out.append(
                    {
                        "chapter_no": chapter["chapter_no"],
                        "section_no": section["section_no"],
                        "section_title": section["section_title"],
                        "narrative_blocks": [
                            {
                                "block_id": f"b-{chapter['chapter_no']}-{section['section_no']}-1",
                                "type": "narration",
                                "text": "The scene opens.",
                                "speaker_id": None,
                            },
                            {
                                "block_id": f"b-{chapter['chapter_no']}-{section['section_no']}-2",
                                "type": "dialogue",
                                "text": "Let us begin.",
                                "speaker_id": speaker_id,
                            },
                        ],
                    }
                )

        return StepResult(
            output={"scenario_sections": sections_out},
            prompt="Generate scenario sections",
            model=context.config.model_name,
            temperature=context.config.temperature,
            input_tokens=220,
            output_tokens=440,
        )


def build_minimal_steps() -> list[Step]:
    return [
        GenerateCharacterProfilesStep(),
        GenerateOutlineStep(),
        GenerateSectionsStep(),
    ]
