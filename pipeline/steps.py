from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .prompts import resolve_step_prompt
from .schema_validation import StepSchemaValidator
from .scenario_quality import ScenarioBodyQualityChecker
from .section_prompt import ScenarioSectionPromptBuilder
from .types import Step, StepContext, StepResult


class GenerateCharacterProfilesStep(Step):
    name = "step-01-generate-character-profiles"
    schema_name = "step-01-generate-character-profiles.schema.json"
    input_keys = ("character_overviews",)

    def run(self, context: StepContext) -> StepResult:
        prompt = resolve_step_prompt(context, self.name)
        input_data = context.shared_data["input"]
        profiles: list[dict[str, Any]] = []
        for item in input_data["character_overviews"]:
            profiles.append(
                {
                    "character_id": item["character_id"],
                    "name": item["name"],
                    "role": item["role"],
                    "personality": {
                        "core_traits": ["thoughtful"],
                        "values": ["integrity"],
                        "weaknesses": ["hesitation"],
                    },
                    "speech": {
                        "style": item.get("speech_style_hint", "natural"),
                        "first_person": "I",
                        "verbal_tics": [],
                    },
                    "appearance": {
                        "age_impression": item.get("age_range", "adult"),
                        "features": [item.get("appearance_hint", "distinctive")],
                        "costume": "scene-appropriate clothing",
                    },
                    "emotion_model": {
                        "available_expressions": ["neutral", "happy", "sad"],
                    },
                }
            )

        return StepResult(
            output={"character_profiles": profiles},
            prompt=prompt.text,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
            model=context.config.model_name,
            temperature=context.config.temperature_for(self.name),
            input_tokens=120,
            output_tokens=240,
        )


class GenerateOutlineStep(Step):
    name = "step-02-generate-outline"
    schema_name = "step-02-generate-outline.schema.json"
    input_keys = ("scenario_idea", "character_profiles")

    def run(self, context: StepContext) -> StepResult:
        prompt = resolve_step_prompt(context, self.name)
        input_data = context.shared_data["input"]
        profile_ids = [x["character_id"] for x in context.shared_data["character_profiles"]]
        target = input_data["scenario_idea"]["target_length"]

        chapters = []
        for chapter_no in range(1, target["chapter_count"] + 1):
            sections = []
            for section_no in range(1, target["sections_per_chapter"] + 1):
                phase = (chapter_no - 1) * target["sections_per_chapter"] + section_no
                purpose = (
                    f"Develop the theme '{input_data['scenario_idea']['theme']}' "
                    f"through story phase {phase}"
                )
                sections.append(
                    {
                        "section_no": section_no,
                        "section_title": f"Section {chapter_no}-{section_no}",
                        "section_purpose": purpose,
                        "key_events": [
                            f"phase-{phase}-conflict emerges",
                            f"phase-{phase}-choice changes the situation",
                        ],
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
            prompt=prompt.text,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
            model=context.config.model_name,
            temperature=context.config.temperature_for(self.name),
            input_tokens=180,
            output_tokens=360,
        )


class GenerateSectionsStep(Step):
    name = "step-03-generate-sections"
    schema_name = "step-04-generate-sections.schema.json"
    input_keys = ("character_profiles", "scenario_outline")

    def run(self, context: StepContext) -> StepResult:
        return self._run(context, retry_feedback=None)

    def run_with_prompt_revision(
        self, context: StepContext, failure_reason: str
    ) -> StepResult:
        return self._run(context, retry_feedback=failure_reason)

    def run_fallback(self, context: StepContext, failure_reason: str) -> StepResult:
        return self._run(context, retry_feedback=failure_reason)

    def _run(
        self,
        context: StepContext,
        retry_feedback: str | None,
    ) -> StepResult:
        outline = context.shared_data["scenario_outline"]
        scenario_idea = context.shared_data["input"]["scenario_idea"]
        character_profiles = context.shared_data["character_profiles"]
        requested_version = context.config.prompt_versions.get(self.name)
        prompt_builder = ScenarioSectionPromptBuilder()
        schema_validator = StepSchemaValidator()
        quality_checker = ScenarioBodyQualityChecker()
        quality_config = context.config.scenario_body_generation
        valid_character_ids = {
            profile["character_id"] for profile in character_profiles
        }
        rendered_prompts = []
        previous_state: dict[str, Any] = {"status": "story_start"}
        sections_out: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0
        checkpoint_dir = Path(context.artifacts_dir) / "sections"

        for chapter in outline["chapters"]:
            for section in chapter["sections"]:
                rendered_prompt = prompt_builder.build(
                    scenario_idea=scenario_idea,
                    character_profiles=character_profiles,
                    chapter=chapter,
                    section=section,
                    previous_state=previous_state,
                    version=requested_version,
                )
                rendered_prompts.append(rendered_prompt)
                checkpoint_path = checkpoint_dir / (
                    f"chapter-{chapter['chapter_no']:03d}-section-{section['section_no']:03d}.json"
                )
                generated_section = self._load_checkpoint(
                    checkpoint_path,
                    rendered_prompt.rendered_hash,
                    schema_validator,
                    chapter,
                    section,
                    quality_checker,
                    valid_character_ids,
                    quality_config.min_characters,
                    quality_config.max_characters,
                    quality_config.require_event_mentions,
                )
                if generated_section is not None:
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": self.name,
                            "event": "section_checkpoint_loaded",
                            "chapter_no": chapter["chapter_no"],
                            "section_no": section["section_no"],
                        }
                    )
                else:
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": self.name,
                            "event": "section_generation_started",
                            "chapter_no": chapter["chapter_no"],
                            "section_no": section["section_no"],
                        }
                    )
                    generation_prompt = rendered_prompt.text
                    if retry_feedback:
                        generation_prompt += (
                            "\n\nRETRY CORRECTION\n"
                            "The previous attempt failed validation for this pipeline. "
                            "Correct the issue while preserving all other requirements:\n"
                            f"{retry_feedback}"
                        )
                    response = context.text_generation_provider.generate_json(
                        prompt=generation_prompt,
                        model=context.config.text_generation.model,
                        temperature=context.config.temperature_for(self.name),
                    )
                    schema_validator.validate(
                        schema_name=self.schema_name,
                        section="output",
                        instance=response.data,
                    )
                    generated_sections = response.data["scenario_sections"]
                    if len(generated_sections) != 1:
                        raise ValueError("Section generation must return exactly one section")
                    generated_section = generated_sections[0]
                    self._validate_target(generated_section, chapter, section)
                    quality_checker.check_section(
                        generated_section=generated_section,
                        outline_section=section,
                        valid_character_ids=valid_character_ids,
                        min_characters=quality_config.min_characters,
                        max_characters=quality_config.max_characters,
                        require_event_mentions=quality_config.require_event_mentions,
                    )
                    self._write_checkpoint(
                        checkpoint_path,
                        rendered_prompt.rendered_hash,
                        generated_section,
                    )
                    input_tokens += response.input_tokens or 0
                    output_tokens += response.output_tokens or 0
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": self.name,
                            "event": "section_generated",
                            "chapter_no": chapter["chapter_no"],
                            "section_no": section["section_no"],
                            "checkpoint_path": str(checkpoint_path),
                        }
                    )
                sections_out.append(generated_section)
                previous_state = {
                    "previous_chapter_no": chapter["chapter_no"],
                    "previous_section_no": section["section_no"],
                    "previous_key_events": list(section["key_events"]),
                    "previous_section_summary": " ".join(
                        block["text"] for block in generated_section["narrative_blocks"]
                    ),
                }

        prompt = rendered_prompts[0]

        return StepResult(
            output={"scenario_sections": sections_out},
            prompt=prompt.text,
            prompt_version=prompt.version,
            prompt_hash=prompt.template_hash,
            model=context.config.model_name,
            temperature=context.config.temperature_for(self.name),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata={
                "section_prompt_count": len(rendered_prompts),
                "section_checkpoint_dir": str(checkpoint_dir),
            },
        )

    def _load_checkpoint(
        self,
        path: Path,
        prompt_hash: str,
        validator: StepSchemaValidator,
        chapter: dict[str, Any],
        target_section: dict[str, Any],
        quality_checker: ScenarioBodyQualityChecker,
        valid_character_ids: set[str],
        min_characters: int,
        max_characters: int,
        require_event_mentions: bool,
    ) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("prompt_hash") != prompt_hash:
            return None
        section = payload.get("section")
        try:
            validator.validate(
                schema_name=self.schema_name,
                section="output",
                instance={"scenario_sections": [section]},
            )
            self._validate_target(section, chapter, target_section)
            quality_checker.check_section(
                generated_section=section,
                outline_section=target_section,
                valid_character_ids=valid_character_ids,
                min_characters=min_characters,
                max_characters=max_characters,
                require_event_mentions=require_event_mentions,
            )
        except (KeyError, TypeError, ValueError):
            return None
        return section

    @staticmethod
    def _write_checkpoint(
        path: Path,
        prompt_hash: str,
        section: dict[str, Any],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(
                {"prompt_hash": prompt_hash, "section": section},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp_path.replace(path)

    @staticmethod
    def _validate_target(
        generated: dict[str, Any],
        chapter: dict[str, Any],
        section: dict[str, Any],
    ) -> None:
        expected = (
            chapter["chapter_no"],
            section["section_no"],
            section["section_title"],
        )
        actual = (
            generated["chapter_no"],
            generated["section_no"],
            generated["section_title"],
        )
        if actual != expected:
            raise ValueError(
                f"Generated section does not match target: expected {expected}, got {actual}"
            )


def build_minimal_steps() -> list[Step]:
    return [
        GenerateCharacterProfilesStep(),
        GenerateOutlineStep(),
        GenerateSectionsStep(),
    ]
