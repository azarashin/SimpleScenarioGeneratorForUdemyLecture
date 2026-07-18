from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from string import Template
from typing import Any, Literal

from .prompts import PromptCatalog


ImagePromptKind = Literal["base", "expression", "expression_sheet"]

EXPRESSION_CONCEPTS: tuple[tuple[str, str], ...] = (
    ("neutral", "neutral, calm, readable base expression"),
    ("smile", "gentle friendly smile"),
    ("serious", "focused and stern expression"),
    ("thinking", "thoughtful expression, slightly narrowed eyes"),
    ("surprised", "surprised expression, widened eyes, slightly open mouth"),
    ("worried", "worried expression, lowered eyebrows, uneasy mouth"),
    ("confused", "confused expression, uncertain eyes, slightly open mouth"),
    ("angry", "angry expression, sharp eyes, tense mouth"),
    ("sad", "sad expression, downcast eyes"),
    ("relieved", "relieved expression, soft eyes, small relaxed smile"),
    ("embarrassed", "embarrassed expression, slight blush, shy eyes"),
    ("nervous", "nervous expression, anxious eyes, tense small mouth"),
    ("confident", "confident expression, composed smile, steady eyes"),
    ("doubtful", "doubtful expression, suspicious eyes, slightly tilted eyebrows"),
    ("shocked", "shocked expression, very wide eyes, open mouth"),
    ("determined", "determined expression, strong eyes, firm mouth"),
)


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
            mode_requirements=(
                "Create exactly one centered bust-up portrait with a neutral expression. "
                "This image establishes the canonical identity for the expression sheet."
            ),
            expression_concepts="neutral: neutral, calm, readable base expression",
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
            mode_requirements=(
                "Create exactly one centered bust-up portrait. Change only the requested "
                "facial expression."
            ),
            expression_concepts=f"{expression}: requested expression",
            width=width,
            height=height,
            style_preset=style_preset,
            version=version,
        )

    def build_expression_sheet(
        self,
        *,
        character_profile: dict[str, Any],
        width: int,
        height: int,
        style_preset: str,
        version: str | None = None,
    ) -> RenderedCharacterImagePrompt:
        self._validate_profile(character_profile)
        expressions = character_profile["emotion_model"]["available_expressions"]
        expected = [name for name, _ in EXPRESSION_CONCEPTS]
        if expressions != expected:
            raise ValueError(
                "Expression sheet requires the canonical 4x4 order of 16 expressions: "
                f"{expected}"
            )
        concepts = "\n".join(
            f"{index}. {name}: {description}"
            for index, (name, description) in enumerate(EXPRESSION_CONCEPTS, start=1)
        )
        return self._build(
            character_profile=character_profile,
            kind="expression_sheet",
            expression="expression-sheet-4x4",
            reference_instruction=(
                "Use the supplied canonical base image as the identity reference for "
                "every panel."
            ),
            mode_requirements=(
                "Create exactly 16 bust-up portraits in a strict 4 columns x 4 rows grid.\n"
                "- Treat the canvas as 16 equal cells and center one portrait in each cell.\n"
                "- Keep every head, hair, shoulder, and outfit fully inside its cell.\n"
                "- Keep identical framing, head size, camera angle, lighting, and position.\n"
                "- Use one plain solid background across all cells with no grid lines.\n"
                "- Leave clear background spacing so fixed-coordinate cropping is safe.\n"
                "- Do not merge, overlap, omit, duplicate, or reorder portraits.\n"
                "- Do not include text, labels, numbers, captions, borders, or written marks."
            ),
            expression_concepts=concepts,
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
        mode_requirements: str,
        expression_concepts: str,
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
            "mode_requirements": mode_requirements,
            "expression_concepts": expression_concepts,
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
