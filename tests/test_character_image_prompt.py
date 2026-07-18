from __future__ import annotations

import pytest

from pipeline.character_image_prompt import CharacterImagePromptBuilder


@pytest.fixture
def character_profile() -> dict[str, object]:
    return {
        "character_id": "c001",
        "name": "Aoi",
        "role": "protagonist",
        "personality": {
            "core_traits": ["thoughtful"],
            "values": ["integrity"],
            "weaknesses": ["hesitation"],
        },
        "speech": {
            "style": "natural",
            "first_person": "I",
            "verbal_tics": [],
        },
        "appearance": {
            "age_impression": "young adult",
            "features": ["short blue hair", "amber eyes"],
            "costume": "navy school blazer",
        },
        "emotion_model": {
            "available_expressions": ["neutral", "happy", "sad"],
        },
    }


def test_base_prompt_contains_canonical_visual_inputs(
    character_profile: dict[str, object],
) -> None:
    prompt = CharacterImagePromptBuilder().build_base(
        character_profile=character_profile,
        width=1024,
        height=1024,
        style_preset="anime",
        version="v1",
    )

    assert prompt.character_id == "c001"
    assert prompt.kind == "base"
    assert prompt.expression == "neutral"
    assert prompt.version == "v1"
    assert len(prompt.template_hash) == 64
    assert len(prompt.rendered_hash) == 64
    assert "short blue hair" in prompt.text
    assert "amber eyes" in prompt.text
    assert "navy school blazer" in prompt.text
    assert "1024x1024" in prompt.text
    assert "anime" in prompt.text
    assert "canonical character design" in prompt.text


def test_expression_prompt_prioritizes_identity_consistency(
    character_profile: dict[str, object],
) -> None:
    prompt = CharacterImagePromptBuilder().build_expression(
        character_profile=character_profile,
        expression="happy",
        width=768,
        height=1024,
        style_preset="anime",
    )

    assert prompt.kind == "expression"
    assert prompt.expression == "happy"
    assert "canonical base image" in prompt.text
    assert "change only the facial expression" in prompt.text
    assert "same face, hair, body proportions, costume" in prompt.text
    assert "768x1024" in prompt.text


def test_expression_prompt_rejects_expression_not_in_profile(
    character_profile: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="is not available"):
        CharacterImagePromptBuilder().build_expression(
            character_profile=character_profile,
            expression="angry",
            width=1024,
            height=1024,
            style_preset="anime",
        )


def test_prompt_rejects_invalid_profile_and_rendering_options(
    character_profile: dict[str, object],
) -> None:
    invalid_profile = {**character_profile, "appearance": {"features": []}}
    builder = CharacterImagePromptBuilder()

    with pytest.raises(ValueError, match="missing image prompt fields"):
        builder.build_base(
            character_profile=invalid_profile,
            width=1024,
            height=1024,
            style_preset="anime",
        )
    with pytest.raises(ValueError, match="dimensions must be greater than zero"):
        builder.build_base(
            character_profile=character_profile,
            width=0,
            height=1024,
            style_preset="anime",
        )
