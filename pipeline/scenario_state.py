from __future__ import annotations

from copy import deepcopy
from typing import Any


STATE_VERSION = 1


def create_initial_scenario_state(character_ids: set[str]) -> dict[str, Any]:
    """Create the explicit state passed into the first generated section."""
    return {
        "state_version": STATE_VERSION,
        "current_section": None,
        "character_locations": {
            character_id: None for character_id in sorted(character_ids)
        },
        "possessions": {character_id: [] for character_id in sorted(character_ids)},
        "known_information": [],
        "relationship_changes": [],
        "occurred_events": [],
        "unresolved_plot_threads": [],
        "previous_section": None,
    }


def advance_scenario_state(
    previous_state: dict[str, Any],
    *,
    chapter_no: int,
    outline_section: dict[str, Any],
    generated_section: dict[str, Any],
) -> dict[str, Any]:
    """Carry state forward and retain the complete previous section as evidence."""
    state = deepcopy(previous_state)
    state["state_version"] = STATE_VERSION
    state["current_section"] = {
        "chapter_no": chapter_no,
        "section_no": outline_section["section_no"],
    }
    occurred_events = list(state.get("occurred_events", []))
    for event in outline_section["key_events"]:
        occurred_events.append(
            {
                "chapter_no": chapter_no,
                "section_no": outline_section["section_no"],
                "event": event,
            }
        )
    state["occurred_events"] = occurred_events
    state["previous_section"] = deepcopy(generated_section)
    validate_scenario_state(state)
    return state


def validate_scenario_state(
    state: dict[str, Any],
    *,
    expected_character_ids: set[str] | None = None,
) -> None:
    """Reject incomplete or structurally unsafe persisted state."""
    required = {
        "state_version",
        "current_section",
        "character_locations",
        "possessions",
        "known_information",
        "relationship_changes",
        "occurred_events",
        "unresolved_plot_threads",
        "previous_section",
    }
    if not isinstance(state, dict) or set(state) != required:
        raise ValueError("Scenario state has missing or unknown fields")
    if state["state_version"] != STATE_VERSION:
        raise ValueError("Unsupported scenario state version")
    if not isinstance(state["character_locations"], dict):
        raise ValueError("Scenario character_locations must be an object")
    if not isinstance(state["possessions"], dict) or any(
        not isinstance(items, list) for items in state["possessions"].values()
    ):
        raise ValueError("Scenario possessions must map character IDs to arrays")
    if expected_character_ids is not None:
        if set(state["character_locations"]) != expected_character_ids:
            raise ValueError("Scenario character_locations has inconsistent character IDs")
        if set(state["possessions"]) != expected_character_ids:
            raise ValueError("Scenario possessions has inconsistent character IDs")
    for field in (
        "known_information",
        "relationship_changes",
        "occurred_events",
        "unresolved_plot_threads",
    ):
        if not isinstance(state[field], list):
            raise ValueError(f"Scenario {field} must be an array")
    if state["current_section"] is not None and not isinstance(
        state["current_section"], dict
    ):
        raise ValueError("Scenario current_section must be an object or null")
    if state["previous_section"] is not None and not isinstance(
        state["previous_section"], dict
    ):
        raise ValueError("Scenario previous_section must be an object or null")
