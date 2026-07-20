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
        subsection=section["subsections"][0],
        previous_state=previous_state,
        version="v2",
    )

    assert prompt.version == "v2"
    assert len(prompt.template_hash) == 64
    assert len(prompt.rendered_hash) == 64
    assert context.shared_data["input"]["scenario_idea"]["title"] in prompt.text
    assert context.shared_data["input"]["scenario_idea"]["theme"] in prompt.text
    assert context.shared_data["input"]["scenario_idea"]["premise"] in prompt.text
    assert section["section_purpose"] in prompt.text
    assert all(
        event["description"] in prompt.text for event in section["key_events"]
    )
    assert section["participating_characters"][0] in prompt.text
    assert "the key is missing" in prompt.text
    assert "scenario-sections.schema.json" in prompt.text
    assert "narration requires speaker_id=null" in prompt.text
    assert "dialogue requires a speaker_id" in prompt.text
    assert "6 to 14 dialogue blocks" in prompt.text
    assert "TARGET SUBSECTION" in prompt.text
    assert "Populate state_updates with every durable fact" in prompt.text
    assert "compact cumulative state is authoritative" in prompt.text
    assert "Start after recent_context" in prompt.text
    assert "never recreate its opening" in prompt.text
    assert "must_not_repeat" in prompt.text
    assert "planned_state_updates is a binding prose contract" in prompt.text
    assert "plan_progress records already executed" in prompt.text
    assert "Aim for approximately 1200 non-whitespace characters" in prompt.text
    assert "Accepted length is 900 to 1600 non-whitespace characters" in prompt.text
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
            subsection={
                "subsection_no": 1,
                "subsection_title": "Beat",
                "subsection_purpose": "Test",
                "key_events": [
                    {
                        "event_id": "phase-1-beat-1",
                        "description": "A new event occurs.",
                    }
                ],
                "start_state": "The previous scene is complete.",
                "state_change": {
                    "event_id": "phase-1-beat-1",
                    "description": "A new event occurs.",
                },
                "end_state": "The event is complete.",
                "must_not_repeat": ["Do not replay the previous scene."],
            },
            previous_state={},
            version="v2",
        )


def test_section_prompt_excludes_non_participating_character_profiles(make_context) -> None:
    context, chapter, section = _prepared_context(make_context)
    profiles = [
        *context.shared_data["character_profiles"],
        {
            "character_id": "c999",
            "name": "OFFSTAGE-PROFILE-MARKER",
            "role": "later character",
        },
    ]

    prompt = ScenarioSectionPromptBuilder().build(
        scenario_idea=context.shared_data["input"]["scenario_idea"],
        character_profiles=profiles,
        chapter=chapter,
        section=section,
        subsection=section["subsections"][0],
        previous_state={},
        version="v2",
    )

    assert "OFFSTAGE-PROFILE-MARKER" not in prompt.text
