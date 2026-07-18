from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any

from PIL import Image

from .asset_manager import CharacterAssetResolver
from .artifact_loader import PipelineArtifactLoader
from .character_image_prompt import CharacterImagePromptBuilder, EXPRESSION_CONCEPTS
from .consistency import PipelineConsistencyChecker
from .errors import ScenarioGenerationFallbackError
from .prompts import resolve_step_prompt
from .schema_validation import StepSchemaValidator
from .scenario_quality import ScenarioBodyQualityChecker
from .scenario_state import (
    advance_scenario_state,
    create_initial_scenario_state,
    validate_scenario_state,
)
from .section_prompt import ScenarioSectionPromptBuilder
from .html_templates import render_chapter_page, render_index_page, render_section_page
from .html_output import HtmlOutputWriter
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
                        "available_expressions": [
                            name for name, _ in EXPRESSION_CONCEPTS
                        ],
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


class GenerateCharacterImagesStep(Step):
    name = "step-03-generate-character-images"
    schema_name = "step-03-generate-character-images.schema.json"
    input_keys = ("character_profiles",)

    def run(self, context: StepContext) -> StepResult:
        profiles = context.shared_data["character_profiles"]
        config = context.config.image_generation
        requested_version = context.config.prompt_versions.get(self.name)
        prompt_builder = CharacterImagePromptBuilder()
        run_root = Path(context.artifacts_dir).parent
        checkpoint_root = Path(context.artifacts_dir) / "images"
        assets: list[dict[str, Any]] = []
        rendered_prompts = []

        for profile in profiles:
            character_id = profile["character_id"]
            character_dir = run_root / "assets" / "characters" / character_id
            base_prompt = prompt_builder.build_base(
                character_profile=profile,
                width=config.width,
                height=config.height,
                style_preset=config.style_preset,
                version=requested_version,
            )
            rendered_prompts.append(base_prompt)
            base_relative = self._resolve_image(
                context=context,
                prompt=base_prompt,
                label="base",
                image_stem="base",
                character_id=character_id,
                character_dir=character_dir,
                checkpoint_path=checkpoint_root / character_id / "base.json",
                run_root=run_root,
                reference_image_path=None,
                width=config.width,
                height=config.height,
            )
            sheet_prompt = prompt_builder.build_expression_sheet(
                character_profile=profile,
                width=config.expression_sheet_width,
                height=config.expression_sheet_height,
                style_preset=config.style_preset,
                version=requested_version,
            )
            rendered_prompts.append(sheet_prompt)
            sheet_relative = self._resolve_image(
                context=context,
                prompt=sheet_prompt,
                label="expression-sheet",
                image_stem="expression-sheet",
                character_id=character_id,
                character_dir=character_dir,
                checkpoint_path=checkpoint_root
                / character_id
                / "expression-sheet.json",
                run_root=run_root,
                reference_image_path=run_root / base_relative,
                width=config.expression_sheet_width,
                height=config.expression_sheet_height,
            )
            expression_images = self._crop_expression_sheet(
                context=context,
                character_id=character_id,
                expressions=profile["emotion_model"]["available_expressions"],
                sheet_path=run_root / sheet_relative,
                character_dir=character_dir,
                checkpoint_dir=checkpoint_root / character_id / "expressions",
                run_root=run_root,
            )

            assets.append(
                {
                    "character_id": character_id,
                    "base_image_path": base_relative,
                    "expression_images": expression_images,
                }
            )

        prompt_hash = hashlib.sha256(
            "\n".join(prompt.rendered_hash for prompt in rendered_prompts).encode("utf-8")
        ).hexdigest()
        first_prompt = rendered_prompts[0]
        return StepResult(
            output={"character_image_assets": assets},
            prompt=first_prompt.text,
            prompt_version=first_prompt.version,
            prompt_hash=prompt_hash,
            model=config.model,
            metadata={
                "image_count": len(rendered_prompts),
                "character_count": len(assets),
                "assets_root": "assets/characters",
                "checkpoint_root": "artifacts/images",
                "expression_sheet_count": len(assets),
                "expression_crop_count": sum(
                    len(asset["expression_images"]) for asset in assets
                ),
            },
        )

    def _resolve_image(
        self,
        *,
        context: StepContext,
        prompt: Any,
        label: str,
        image_stem: str,
        character_id: str,
        character_dir: Path,
        checkpoint_path: Path,
        run_root: Path,
        reference_image_path: Path | None,
        width: int,
        height: int,
    ) -> str:
        config = context.config.image_generation
        reference_image_bytes = (
            reference_image_path.read_bytes()
            if reference_image_path is not None
            else None
        )
        reference_image_hash = (
            hashlib.sha256(reference_image_bytes).hexdigest()
            if reference_image_bytes is not None
            else None
        )
        request_hash = self._request_hash(
            rendered_hash=prompt.rendered_hash,
            provider=config.provider,
            model=config.model,
            width=width,
            height=height,
            style_preset=config.style_preset,
            quality=config.quality,
            output_format=config.output_format,
            label=label,
            reference_image_hash=reference_image_hash,
        )
        if not context.force:
            checkpoint_image = self._load_image_checkpoint(
                checkpoint_path=checkpoint_path,
                expected_request_hash=request_hash,
                run_root=run_root,
                expected_width=width,
                expected_height=height,
            )
            if checkpoint_image is not None:
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": self.name,
                        "event": "image_checkpoint_loaded",
                        "character_id": character_id,
                        "expression": label,
                        "image_path": checkpoint_image,
                    }
                )
                return checkpoint_image

        response = context.image_generation_provider.generate_image(
            prompt=prompt.text,
            model=config.model,
            width=width,
            height=height,
            style_preset=config.style_preset,
            reference_image_bytes=reference_image_bytes,
            reference_mime_type=(
                self._mime_type_for_path(reference_image_path)
                if reference_image_path is not None
                else None
            ),
        )
        self._validate_image_content(
            response.image_bytes,
            mime_type=response.mime_type,
            expected_width=width,
            expected_height=height,
        )
        image_path = character_dir / (
            f"{image_stem}{self._extension_for(response.mime_type)}"
        )
        self._write_image(image_path, response.image_bytes)
        relative_path = image_path.relative_to(run_root).as_posix()
        self._write_image_checkpoint(
            checkpoint_path=checkpoint_path,
            request_hash=request_hash,
            image_hash=hashlib.sha256(response.image_bytes).hexdigest(),
            image_path=relative_path,
            mime_type=response.mime_type,
            model=response.model,
        )
        context.trace_logger.log(
            {
                "run_id": context.run_id,
                "step": self.name,
                "event": "image_generated",
                "character_id": character_id,
                "expression": label,
                "image_path": relative_path,
                "checkpoint_path": str(checkpoint_path),
            }
        )
        return relative_path

    def _crop_expression_sheet(
        self,
        *,
        context: StepContext,
        character_id: str,
        expressions: list[str],
        sheet_path: Path,
        character_dir: Path,
        checkpoint_dir: Path,
        run_root: Path,
    ) -> dict[str, str]:
        if len(expressions) != 16:
            raise ValueError("A 4x4 expression sheet requires exactly 16 expressions")
        config = context.config.image_generation
        crop_width = config.expression_sheet_width // 4
        crop_height = config.expression_sheet_height // 4
        sheet_bytes = sheet_path.read_bytes()
        sheet_hash = hashlib.sha256(sheet_bytes).hexdigest()
        extension = self._extension_for_output_format(config.output_format)
        mime_type = self._mime_type_for_output_format(config.output_format)
        expression_images: dict[str, str] = {}

        with Image.open(BytesIO(sheet_bytes)) as sheet:
            sheet.load()
            if sheet.size != (
                config.expression_sheet_width,
                config.expression_sheet_height,
            ):
                raise ValueError(
                    "Expression sheet dimensions differ from config: "
                    f"{sheet.width}x{sheet.height}"
                )
            for index, expression in enumerate(expressions):
                row, column = divmod(index, 4)
                image_stem = self._expression_filename(expression)
                image_path = (
                    character_dir / "expressions" / f"{image_stem}{extension}"
                )
                checkpoint_path = checkpoint_dir / f"{image_stem}.json"
                request_hash = self._request_hash(
                    rendered_hash=sheet_hash,
                    provider="derived-grid-crop",
                    model=config.model,
                    width=crop_width,
                    height=crop_height,
                    style_preset=config.style_preset,
                    quality=config.quality,
                    output_format=config.output_format,
                    label=f"{index}:{expression}",
                    reference_image_hash=sheet_hash,
                )
                if not context.force:
                    checkpoint_image = self._load_image_checkpoint(
                        checkpoint_path=checkpoint_path,
                        expected_request_hash=request_hash,
                        run_root=run_root,
                        expected_width=crop_width,
                        expected_height=crop_height,
                    )
                    if checkpoint_image is not None:
                        expression_images[expression] = checkpoint_image
                        context.trace_logger.log(
                            {
                                "run_id": context.run_id,
                                "step": self.name,
                                "event": "expression_crop_checkpoint_loaded",
                                "character_id": character_id,
                                "expression": expression,
                                "image_path": checkpoint_image,
                            }
                        )
                        continue

                box = (
                    column * crop_width,
                    row * crop_height,
                    (column + 1) * crop_width,
                    (row + 1) * crop_height,
                )
                crop_bytes = self._encode_crop(
                    sheet.crop(box), output_format=config.output_format
                )
                self._write_image(image_path, crop_bytes)
                relative_path = image_path.relative_to(run_root).as_posix()
                self._write_image_checkpoint(
                    checkpoint_path=checkpoint_path,
                    request_hash=request_hash,
                    image_hash=hashlib.sha256(crop_bytes).hexdigest(),
                    image_path=relative_path,
                    mime_type=mime_type,
                    model="derived-grid-crop",
                )
                expression_images[expression] = relative_path
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": self.name,
                        "event": "expression_crop_generated",
                        "character_id": character_id,
                        "expression": expression,
                        "cell_index": index,
                        "image_path": relative_path,
                    }
                )
        return expression_images

    @staticmethod
    def _encode_crop(image: Image.Image, *, output_format: str) -> bytes:
        buffer = BytesIO()
        if output_format == "jpeg" and image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        pillow_format = {
            "png": "PNG",
            "jpeg": "JPEG",
            "webp": "WEBP",
        }[output_format]
        image.save(buffer, format=pillow_format)
        return buffer.getvalue()

    @staticmethod
    def _extension_for_output_format(output_format: str) -> str:
        return {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}[output_format]

    @staticmethod
    def _mime_type_for_output_format(output_format: str) -> str:
        return {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }[output_format]

    @staticmethod
    def _request_hash(
        *,
        rendered_hash: str,
        provider: str,
        model: str,
        width: int,
        height: int,
        style_preset: str,
        quality: str,
        output_format: str,
        label: str,
        reference_image_hash: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "rendered_hash": rendered_hash,
                "provider": provider,
                "model": model,
                "width": width,
                "height": height,
                "style_preset": style_preset,
                "quality": quality,
                "output_format": output_format,
                "label": label,
                "reference_image_hash": reference_image_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_image_checkpoint(
        *,
        checkpoint_path: Path,
        expected_request_hash: str,
        run_root: Path,
        expected_width: int,
        expected_height: int,
    ) -> str | None:
        if not checkpoint_path.is_file():
            return None
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint["request_hash"] != expected_request_hash:
                return None
            relative_path = checkpoint["image_path"]
            if not isinstance(relative_path, str):
                return None
            image_path = (run_root / relative_path).resolve()
            image_path.relative_to(run_root.resolve())
            image_bytes = image_path.read_bytes()
            if not image_bytes:
                return None
            if hashlib.sha256(image_bytes).hexdigest() != checkpoint["image_hash"]:
                return None
            GenerateCharacterImagesStep._validate_image_content(
                image_bytes,
                mime_type=checkpoint["mime_type"],
                expected_width=expected_width,
                expected_height=expected_height,
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            return None
        return relative_path

    @staticmethod
    def _validate_image_content(
        image_bytes: bytes,
        *,
        mime_type: str,
        expected_width: int,
        expected_height: int,
    ) -> None:
        image_format, width, height = PipelineConsistencyChecker._image_info(
            image_bytes
        )
        expected_format = {
            "image/png": "png",
            "image/jpeg": "jpeg",
            "image/webp": "webp",
        }.get(mime_type.casefold())
        if expected_format != image_format:
            raise ValueError(
                f"Generated image MIME type {mime_type!r} does not match "
                f"its {image_format} content"
            )
        if (width, height) != (expected_width, expected_height):
            raise ValueError(
                "Generated image dimensions differ from config: "
                f"{width}x{height} != {expected_width}x{expected_height}"
            )

    @staticmethod
    def _write_image_checkpoint(
        *,
        checkpoint_path: Path,
        request_hash: str,
        image_hash: str,
        image_path: str,
        mime_type: str,
        model: str,
    ) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = checkpoint_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(
                {
                    "request_hash": request_hash,
                    "image_hash": image_hash,
                    "image_path": image_path,
                    "mime_type": mime_type,
                    "model": model,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(checkpoint_path)

    @staticmethod
    def _write_image(path: Path, image_bytes: bytes) -> None:
        if not image_bytes:
            raise ValueError(f"Image provider returned empty content for {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        temporary_path.write_bytes(image_bytes)
        temporary_path.replace(path)

    @staticmethod
    def _extension_for(mime_type: str) -> str:
        extensions = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        try:
            return extensions[mime_type.casefold()]
        except KeyError as exc:
            raise ValueError(f"Unsupported generated image MIME type: {mime_type}") from exc

    @staticmethod
    def _mime_type_for_path(path: Path) -> str:
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        try:
            return mime_types[path.suffix.casefold()]
        except KeyError as exc:
            raise ValueError(f"Unsupported reference image extension: {path.suffix}") from exc

    @staticmethod
    def _expression_filename(expression: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", expression).strip("-_").lower()
        if normalized:
            return normalized
        digest = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:12]
        return f"expression-{digest}"


class GenerateSectionsStep(Step):
    name = "step-04-generate-sections"
    schema_name = "step-04-generate-sections.schema.json"
    input_keys = ("character_profiles", "scenario_outline")

    def run(self, context: StepContext) -> StepResult:
        return self._run(context, retry_feedback=None)

    def run_with_prompt_revision(
        self, context: StepContext, failure_reason: str
    ) -> StepResult:
        return self._run(context, retry_feedback=failure_reason)

    def run_fallback(self, context: StepContext, failure_reason: str) -> StepResult:
        raise ScenarioGenerationFallbackError(
            "Scenario section generation exhausted retries; no synthetic fallback "
            f"content was saved. Last error: {failure_reason}"
        )

    def retry_phase_for_error(self, error: Exception) -> str | None:
        if self._is_transient_provider_error(error):
            return "short_retry"
        if isinstance(error, ValueError):
            return "prompt_revision"
        return None

    @staticmethod
    def _is_transient_provider_error(error: Exception) -> bool:
        if isinstance(error, (ConnectionError, TimeoutError)):
            return True
        error_name = type(error).__name__.casefold()
        return "timeout" in error_name or "connection" in error_name

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
        previous_state = create_initial_scenario_state(valid_character_ids)
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
                    min_characters=quality_config.min_characters,
                    max_characters=quality_config.max_characters,
                    min_dialogue_blocks=quality_config.min_dialogue_blocks,
                    max_dialogue_blocks=quality_config.max_dialogue_blocks,
                    version=requested_version,
                )
                rendered_prompts.append(rendered_prompt)
                checkpoint_path = checkpoint_dir / (
                    f"chapter-{chapter['chapter_no']:03d}-section-{section['section_no']:03d}.json"
                )
                checkpoint = self._load_checkpoint(
                    checkpoint_path,
                    rendered_prompt.rendered_hash,
                    schema_validator,
                    chapter,
                    section,
                    quality_checker,
                    valid_character_ids,
                    quality_config.min_characters,
                    quality_config.max_characters,
                    quality_config.min_dialogue_blocks,
                    quality_config.max_dialogue_blocks,
                    quality_config.require_event_mentions,
                )
                if checkpoint is not None:
                    generated_section, state_after = checkpoint
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
                        generation_prompt += self._prompt_revision(retry_feedback)
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
                    try:
                        quality_checker.check_section(
                            generated_section=generated_section,
                            outline_section=section,
                            valid_character_ids=valid_character_ids,
                            min_characters=quality_config.min_characters,
                            max_characters=quality_config.max_characters,
                            min_dialogue_blocks=quality_config.min_dialogue_blocks,
                            max_dialogue_blocks=quality_config.max_dialogue_blocks,
                            require_event_mentions=quality_config.require_event_mentions,
                        )
                    except ValueError as exc:
                        rejected_path = checkpoint_path.with_name(
                            f"{checkpoint_path.stem}.rejected.json"
                        )
                        rejected_path.parent.mkdir(parents=True, exist_ok=True)
                        metrics = self._section_metrics(generated_section)
                        rejected_path.write_text(
                            json.dumps(
                                {
                                    "failure_reason": str(exc),
                                    "metrics": metrics,
                                    "required": {
                                        "min_characters": quality_config.min_characters,
                                        "max_characters": quality_config.max_characters,
                                        "min_dialogue_blocks": quality_config.min_dialogue_blocks,
                                        "max_dialogue_blocks": quality_config.max_dialogue_blocks,
                                    },
                                    "section": generated_section,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                        context.trace_logger.log(
                            {
                                "run_id": context.run_id,
                                "step": self.name,
                                "event": "section_validation_failed",
                                "chapter_no": chapter["chapter_no"],
                                "section_no": section["section_no"],
                                "failure_reason": str(exc),
                                "metrics": metrics,
                                "rejected_path": str(rejected_path),
                            }
                        )
                        raise
                    state_after = advance_scenario_state(
                        previous_state,
                        chapter_no=chapter["chapter_no"],
                        outline_section=section,
                        generated_section=generated_section,
                    )
                    self._write_checkpoint(
                        checkpoint_path,
                        rendered_prompt.rendered_hash,
                        generated_section,
                        state_after,
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
                previous_state = state_after

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
        min_dialogue_blocks: int,
        max_dialogue_blocks: int,
        require_event_mentions: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("prompt_hash") != prompt_hash:
            return None
        section = payload.get("section")
        state_after = payload.get("state_after")
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
                min_dialogue_blocks=min_dialogue_blocks,
                max_dialogue_blocks=max_dialogue_blocks,
                require_event_mentions=require_event_mentions,
            )
            validate_scenario_state(
                state_after,
                expected_character_ids=valid_character_ids,
            )
            if state_after["previous_section"] != section:
                raise ValueError("Checkpoint state does not match its generated section")
        except (KeyError, TypeError, ValueError):
            return None
        return section, state_after

    @staticmethod
    def _section_metrics(section: dict[str, Any]) -> dict[str, int]:
        blocks = section.get("narrative_blocks", [])
        return {
            "non_whitespace_characters": sum(
                not character.isspace()
                for block in blocks
                for character in str(block.get("text", ""))
            ),
            "narration_blocks": sum(block.get("type") == "narration" for block in blocks),
            "dialogue_blocks": sum(block.get("type") == "dialogue" for block in blocks),
            "total_blocks": len(blocks),
        }

    @staticmethod
    def _prompt_revision(failure_reason: str) -> str:
        reasons = [line.strip() for line in failure_reason.splitlines() if line.strip()]
        bullet_list = "\n".join(f"- {reason}" for reason in reasons)
        return (
            "\n\nPROMPT REVISION\n"
            "前回の生成結果には次の問題がありました:\n"
            f"{bullet_list}\n\n"
            "問題を修正し、他の要件を維持したJSONだけを再出力してください。"
        )

    @staticmethod
    def _write_checkpoint(
        path: Path,
        prompt_hash: str,
        section: dict[str, Any],
        state_after: dict[str, Any],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(
                {
                    "prompt_hash": prompt_hash,
                    "section": section,
                    "state_after": state_after,
                },
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


class GenerateDialogueTagsStep(Step):
    name = "step-05-generate-dialogue-tags"
    schema_name = "step-05-generate-dialogue-tags.schema.json"
    input_keys = ("character_profiles", "scenario_sections")

    def run(self, context: StepContext) -> StepResult:
        profiles = {
            profile["character_id"]: profile
            for profile in context.shared_data["character_profiles"]
        }
        tags: list[dict[str, Any]] = []
        for section in context.shared_data["scenario_sections"]:
            for block in section["narrative_blocks"]:
                if block["type"] != "dialogue":
                    continue
                speaker_id = block["speaker_id"]
                profile = profiles[speaker_id]
                expression, reason = self._infer_expression(
                    str(block["text"]),
                    profile["emotion_model"]["available_expressions"],
                )
                tags.append(
                    {
                        "chapter_no": section["chapter_no"],
                        "section_no": section["section_no"],
                        "block_id": block["block_id"],
                        "speaker_id": speaker_id,
                        "expression": expression,
                        "emotion_reason": reason,
                    }
                )

        return StepResult(
            output={"dialogue_expression_tags": tags},
            model="deterministic-rule-based",
            temperature=context.config.temperature_for(self.name),
            metadata={"dialogue_tag_count": len(tags)},
        )

    @staticmethod
    def _infer_expression(text: str, available: list[str]) -> tuple[str, str]:
        normalized = text.casefold()
        rules = (
            ("angry", ("怒", "許さ", "ふざけ", "damn", "angry"), "怒りを示す語句"),
            ("sad", ("悲", "つら", "泣", "ごめん", "sad"), "悲しみを示す語句"),
            ("worried", ("心配", "不安", "大丈夫", "worry"), "心配を示す語句"),
            ("confused", ("どういう", "わから", "なぜ", "どうして", "?", "？"), "疑問を示す語句"),
            ("surprised", ("まさか", "本当", "えっ", "!", "！"), "驚きを示す語句"),
            ("smile", ("ありがとう", "うれし", "よかった", "笑", "glad"), "肯定的な語句"),
            ("determined", ("必ず", "絶対", "やる", "進もう"), "決意を示す語句"),
        )
        available_set = set(available)
        for expression, markers, reason in rules:
            if expression in available_set and any(marker in normalized for marker in markers):
                return expression, reason
        if "neutral" in available_set:
            return "neutral", "明確な感情表現がないため中立"
        return available[0], "利用可能な表情から既定値を選択"


class RenderHtmlStep(Step):
    name = "step-06-render-html"
    schema_name = "step-06-render-html.schema.json"
    input_keys = (
        "scenario_outline",
        "scenario_sections",
        "dialogue_expression_tags",
        "character_image_assets",
    )

    def prepare_context(self, context: StepContext) -> None:
        loaded = PipelineArtifactLoader(Path(context.artifacts_dir)).load_missing(
            context.shared_data,
            required_keys=self.input_keys,
            optional_keys=("character_profiles",),
        )
        if loaded:
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": self.name,
                    "event": "artifacts_auto_loaded",
                    "outputs": list(loaded),
                }
            )

    def run(self, context: StepContext) -> StepResult:
        outline = context.shared_data["scenario_outline"]
        sections = context.shared_data["scenario_sections"]
        tags = context.shared_data["dialogue_expression_tags"]
        run_root = Path(context.artifacts_dir).parent
        writer = HtmlOutputWriter(run_root)
        resolver = CharacterAssetResolver.from_pipeline_data(
            context.shared_data,
            run_root=run_root,
            verify_files=True,
        )
        sections_by_location = {
            (section["chapter_no"], section["section_no"]): section
            for section in sections
        }

        index_relative = writer.write("index.html", render_index_page(outline=outline))
        chapter_pages: list[dict[str, Any]] = []
        section_pages: list[dict[str, Any]] = []

        for chapter in outline["chapters"]:
            chapter_no = int(chapter["chapter_no"])
            chapter_dir = f"chapter-{chapter_no}"
            chapter_relative = writer.write(
                f"{chapter_dir}/index.html",
                render_chapter_page(
                    work_title=outline["title"],
                    chapter=chapter,
                    outline=outline,
                ),
            )
            chapter_pages.append(
                {"chapter_no": chapter_no, "path": chapter_relative}
            )

            for outline_section in chapter["sections"]:
                section_no = int(outline_section["section_no"])
                section = sections_by_location[(chapter_no, section_no)]
                section_relative = writer.write(
                    f"{chapter_dir}/section-{section_no}.html",
                    render_section_page(
                        work_title=outline["title"],
                        chapter=chapter,
                        section=section,
                        dialogue_tags=tags,
                        asset_resolver=resolver,
                        outline=outline,
                    ),
                )
                section_pages.append(
                    {
                        "chapter_no": chapter_no,
                        "section_no": section_no,
                        "path": section_relative,
                    }
                )

        rendering = []
        for tag in tags:
            resolved = resolver.resolve(
                tag["speaker_id"],
                tag["expression"],
                relative_to=f"chapter-{tag['chapter_no']}",
            )
            rendering.append(
                {
                    "chapter_no": tag["chapter_no"],
                    "section_no": tag["section_no"],
                    "block_id": tag["block_id"],
                    "speaker_id": tag["speaker_id"],
                    "speaker_name": resolved.speaker_name,
                    "expression": tag["expression"],
                    "image_path": resolved.image_path,
                    "alt": resolved.alt,
                    "is_fallback": resolved.is_fallback,
                }
            )

        return StepResult(
            output={
                "rendered_html_pages": {
                    "index_path": index_relative,
                    "chapter_pages": chapter_pages,
                    "section_pages": section_pages,
                },
                "dialogue_speaker_image_rendering": rendering,
            },
            model="deterministic-template-renderer",
            temperature=context.config.temperature_for(self.name),
            metadata={
                "html_page_count": 1 + len(chapter_pages) + len(section_pages),
                "dialogue_rendering_count": len(rendering),
            },
        )


def build_minimal_steps() -> list[Step]:
    return [
        GenerateCharacterProfilesStep(),
        GenerateOutlineStep(),
        GenerateCharacterImagesStep(),
        GenerateSectionsStep(),
        GenerateDialogueTagsStep(),
        RenderHtmlStep(),
    ]
