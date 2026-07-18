from __future__ import annotations

import json
from pathlib import Path

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
    }


def test_scenario_state_carries_structured_fields_and_full_previous_section() -> None:
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
        outline_section={"section_no": 1, "key_events": ["library arrival"]},
        generated_section=section,
    )

    validate_scenario_state(state)
    assert state["character_locations"]["c001"] == "station"
    assert state["possessions"]["c001"] == ["key"]
    assert state["known_information"] == ["the archive is locked"]
    assert state["relationship_changes"] == ["c001 now trusts c002"]
    assert state["unresolved_plot_threads"] == ["who sent the letter"]
    assert state["occurred_events"][-1]["event"] == "library arrival"
    assert state["previous_section"] == section


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
    assert state["previous_section"] == payload["section"]
    assert len(state["occurred_events"]) == 2
    assert "character_locations" in provider.requests[1].prompt
    assert payload["section"]["narrative_blocks"][0]["text"] in (
        provider.requests[1].prompt
    )
