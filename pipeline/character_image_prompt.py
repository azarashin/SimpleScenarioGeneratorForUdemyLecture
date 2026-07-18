from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from string import Template
from typing import Any, Literal

from .prompts import PromptCatalog


ImagePromptKind = Literal["base", "expression"]


@dataclass(frozen=True, slots=True)
class RenderedCharacterImagePrompt:
    text: str
    version: str
    template_hash: str
    rendered_hash: str
    character_id: str
    kind: ImagePromptKind
    expression: str


class CharacterImagePromptBuilder:
    step_name = "step-03-generate-character-images"

    def __init__(self, *, catalog: PromptCatalog | None = None) -> None:
        self.catalog = catalog or PromptCatalog()

    def build_base(
        self,
        *,
        character_profile: dict[str, Any],
        width: int,
        height: int,
        style_preset: str,
        version: str | None = None,
    ) -> RenderedCharacterImagePrompt:
        self._validate_profile(character_profile)
        return self._build(
            character_profile=character_profile,
            kind="base",
            expression="neutral",
            reference_instruction=(
                "No reference image is supplied. Establish the canonical character "
                "design for all later expression variants."
            ),
            width=width,
            height=height,
            style_preset=style_preset,
            version=version,
        )

    def build_expression(
        self,
        *,
        character_profile: dict[str, Any],
        expression: str,
        width: int,
        height: int,
        style_preset: str,
        version: str | None = None,
    ) -> RenderedCharacterImagePrompt:
        self._validate_profile(character_profile)
        available = character_profile["emotion_model"]["available_expressions"]
        if expression not in available:
            raise ValueError(
                f"Expression {expression!r} is not available for character "
                f"{character_profile['character_id']!r}"
            )
        return self._build(
            character_profile=character_profile,
            kind="expression",
            expression=expression,
            reference_instruction=(
                "Use the canonical base image as the identity reference. Preserve the "
                "same face, hair, body proportions, costume, colors, camera, lighting, "
                "and composition; change only the facial expression and the minimal "
                "pose details required to communicate it."
            ),
            width=width,
            height=height,
            style_preset=style_preset,
            version=version,
        )

    def _build(
        self,
        *,
        character_profile: dict[str, Any],
        kind: ImagePromptKind,
        expression: str,
        reference_instruction: str,
        width: int,
        height: int,
        style_preset: str,
        version: str | None,
    ) -> RenderedCharacterImagePrompt:
        if width <= 0 or height <= 0:
            raise ValueError("Image prompt dimensions must be greater than zero")
        if not style_preset.strip():
            raise ValueError("Image prompt style preset must not be empty")

        definition = self.catalog.resolve(self.step_name, version)
        variables = {
            "generation_mode": kind,
            "character_profile_json": json.dumps(
                character_profile, ensure_ascii=False, indent=2, sort_keys=True
            ),
            "target_expression": expression,
            "reference_image_instruction": reference_instruction,
            "width": str(width),
            "height": str(height),
            "style_preset": style_preset,
        }
        try:
            text = Template(definition.text).substitute(variables)
        except KeyError as exc:
            raise ValueError(
                f"Missing prompt template variable in {self.step_name} "
                f"{definition.version}: {exc.args[0]}"
            ) from exc
        return RenderedCharacterImagePrompt(
            text=text,
            version=definition.version,
            template_hash=definition.content_hash,
            rendered_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            character_id=character_profile["character_id"],
            kind=kind,
            expression=expression,
        )

    @staticmethod
    def _validate_profile(character_profile: dict[str, Any]) -> None:
        try:
            character_id = character_profile["character_id"]
            appearance = character_profile["appearance"]
            appearance["age_impression"]
            features = appearance["features"]
            appearance["costume"]
            expressions = character_profile["emotion_model"]["available_expressions"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Character profile is missing image prompt fields") from exc
        if not isinstance(character_id, str) or not character_id.strip():
            raise ValueError("Character profile must contain a non-empty character_id")
        if not isinstance(features, list) or not features:
            raise ValueError("Character appearance features must not be empty")
        if not isinstance(expressions, list) or "neutral" not in expressions:
            raise ValueError("Character expressions must include neutral")
