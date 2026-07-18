from __future__ import annotations

import pytest

from pipeline.engine import StepExecutionEngine
from pipeline.section_prompt import ScenarioSectionPromptBuilder
from pipeline.steps import GenerateCharacterProfilesStep, GenerateOutlineStep


def _prepared_context(make_context):
    context, _ = make_context()
    StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateOutlineStep()]
    ).run(context)
    chapter = context.shared_data["scenario_outline"]["chapters"][0]
    section = chapter["sections"][0]
    return context, chapter, section


def test_section_prompt_contains_all_generation_inputs(make_context) -> None:
    context, chapter, section = _prepared_context(make_context)
    previous_state = {
        "location": "library",
        "known_facts": ["the key is missing"],
    }

    prompt = ScenarioSectionPromptBuilder().build(
        scenario_idea=context.shared_data["input"]["scenario_idea"],
        character_profiles=context.shared_data["character_profiles"],
        chapter=chapter,
        section=section,
        previous_state=previous_state,
        version="v2",
    )

    assert prompt.version == "v2"
    assert len(prompt.template_hash) == 64
    assert context.shared_data["input"]["scenario_idea"]["title"] in prompt.text
    assert context.shared_data["input"]["scenario_idea"]["theme"] in prompt.text
    assert context.shared_data["input"]["scenario_idea"]["premise"] in prompt.text
    assert section["section_purpose"] in prompt.text
    assert section["key_events"][0] in prompt.text
    assert section["participating_characters"][0] in prompt.text
    assert "the key is missing" in prompt.text
    assert "scenario-sections.schema.json" in prompt.text
    assert "narration requires speaker_id=null" in prompt.text
    assert "dialogue requires a speaker_id" in prompt.text
    assert '"maxItems": 1' in prompt.text
    assert '{"scenario_sections": [one target section]}' in prompt.text


def test_section_prompt_rejects_unknown_participating_character(make_context) -> None:
    context, chapter, section = _prepared_context(make_context)
    section = {**section, "participating_characters": ["unknown-character"]}

    with pytest.raises(ValueError, match="unknown character IDs"):
        ScenarioSectionPromptBuilder().build(
            scenario_idea=context.shared_data["input"]["scenario_idea"],
            character_profiles=context.shared_data["character_profiles"],
            chapter=chapter,
            section=section,
            previous_state={},
            version="v2",
        )
