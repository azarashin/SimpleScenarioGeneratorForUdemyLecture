from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.engine import StepExecutionEngine
from pipeline.scenario_state import (
    advance_scenario_state,
    create_initial_scenario_state,
    validate_scenario_state,
)
from pipeline.steps import build_minimal_steps


def _section() -> dict[str, object]:
    return {
        "chapter_no": 1,
        "section_no": 1,
        "section_title": "Arrival",
        "narrative_blocks": [
            {
                "block_id": "b-1-1-1",
                "type": "narration",
                "text": "The character arrives at the library.",
                "speaker_id": None,
            }
        ],
        "state_updates": {
            "character_locations": [
                {"character_id": "c001", "location": "library"}
            ],
            "possessions": [
                {"character_id": "c001", "items": ["key", "archive map"]}
            ],
            "known_information": ["the sealed archive reacts to the key"],
            "relationship_changes": ["c001 now relies on c002"],
            "introduced_entities": [
                {
                    "entity_id": "place-archive",
                    "type": "location",
                    "name": "Sealed Archive",
                    "description": "A restricted room beneath the library",
                }
            ],
            "unresolved_plot_threads": ["who sealed the archive"],
            "resolved_plot_threads": ["who sent the letter"],
            "completed_event_ids": ["phase-1-beat-1"],
            "continuity_summary": "They reached the sealed archive with a new map.",
        },
    }


def test_scenario_state_merges_durable_updates_into_compact_state() -> None:
    initial = create_initial_scenario_state({"c002", "c001"})
    initial["character_locations"]["c001"] = "station"
    initial["possessions"]["c001"] = ["key"]
    initial["known_information"].append("the archive is locked")
    initial["relationship_changes"].append("c001 now trusts c002")
    initial["unresolved_plot_threads"].append("who sent the letter")
    section = _section()

    state = advance_scenario_state(
        initial,
        chapter_no=1,
        subsection_no=1,
        outline_section={
            "section_no": 1,
            "key_events": [
                {
                    "event_id": "phase-1-beat-1",
                    "description": "They arrive at the library.",
                }
            ],
        },
        generated_section=section,
    )

    validate_scenario_state(state)
    assert state["character_locations"]["c001"] == "library"
    assert state["possessions"]["c001"] == ["key", "archive map"]
    assert state["known_information"] == [
        "the archive is locked",
        "the sealed archive reacts to the key",
    ]
    assert state["relationship_changes"] == [
        "c001 now trusts c002",
        "c001 now relies on c002",
    ]
    assert state["unresolved_plot_threads"] == ["who sealed the archive"]
    assert state["introduced_entities"][0]["entity_id"] == "place-archive"
    assert state["occurred_events"][-1]["event_id"] == "phase-1-beat-1"
    assert state["recent_context"] == (
        "They reached the sealed archive with a new map."
    )
    assert state["current_subsection"] == 1


def test_scenario_state_rejects_updates_for_unknown_characters() -> None:
    section = _section()
    section["state_updates"]["character_locations"] = [
        {"character_id": "c999", "location": "elsewhere"}
    ]

    with pytest.raises(ValueError, match="unknown character 'c999'"):
        advance_scenario_state(
            create_initial_scenario_state({"c001", "c002"}),
            chapter_no=1,
            subsection_no=1,
            outline_section={
                "section_no": 1,
                "key_events": [
                    {
                        "event_id": "phase-1-beat-1",
                        "description": "They arrive.",
                    }
                ],
            },
            generated_section=section,
        )


def test_section_checkpoint_state_is_restored_and_passed_to_next_prompt(
    make_context,
) -> None:
    context, _ = make_context()
    context.shared_data["input"]["scenario_idea"]["target_length"] = {
        "chapter_count": 1,
        "sections_per_chapter": 2,
    }
    provider = context.text_generation_provider

    StepExecutionEngine(build_minimal_steps()).run(context)

    first_checkpoint = (
        Path(context.artifacts_dir)
        / "sections"
        / "chapter-001-section-001.json"
    )
    payload = json.loads(first_checkpoint.read_text(encoding="utf-8"))
    state = payload["state_after"]
    validate_scenario_state(state)
    assert state["recent_context"] == payload["section"]["state_updates"][
        "continuity_summary"
    ]
    assert state["current_subsection"] == 1
    assert len(state["occurred_events"]) == 1
    assert "character_locations" in provider.requests[1].prompt
    assert payload["section"]["narrative_blocks"][0]["text"] not in (
        provider.requests[1].prompt
    )
    assert state["recent_context"] in provider.requests[1].prompt
