from __future__ import annotations

from copy import deepcopy
from typing import Any


STATE_VERSION = 3


def create_initial_scenario_state(character_ids: set[str]) -> dict[str, Any]:
    """Create the compact cumulative state passed into scenario generation."""
    return {
        "state_version": STATE_VERSION,
        "current_section": None,
        "current_subsection": None,
        "character_locations": {
            character_id: None for character_id in sorted(character_ids)
        },
        "possessions": {character_id: [] for character_id in sorted(character_ids)},
        "known_information": [],
        "relationship_changes": [],
        "introduced_entities": [],
        "occurred_events": [],
        "unresolved_plot_threads": [],
        "recent_context": None,
    }


def advance_scenario_state(
    previous_state: dict[str, Any],
    *,
    chapter_no: int,
    subsection_no: int,
    outline_section: dict[str, Any],
    generated_section: dict[str, Any],
) -> dict[str, Any]:
    """Merge explicit output updates into compact cumulative story state."""
    state = deepcopy(previous_state)
    state["state_version"] = STATE_VERSION
    state["current_section"] = {
        "chapter_no": chapter_no,
        "section_no": outline_section["section_no"],
    }
    state["current_subsection"] = subsection_no
    updates = generated_section["state_updates"]
    character_ids = set(state["character_locations"])

    for location_update in updates["character_locations"]:
        character_id = location_update["character_id"]
        if character_id not in character_ids:
            raise ValueError(
                f"Scenario state update contains unknown character {character_id!r}"
            )
        state["character_locations"][character_id] = location_update["location"]

    for possession_update in updates["possessions"]:
        character_id = possession_update["character_id"]
        if character_id not in character_ids:
            raise ValueError(
                f"Scenario state update contains unknown character {character_id!r}"
            )
        state["possessions"][character_id] = _unique_strings(
            possession_update["items"]
        )

    state["known_information"] = _merge_strings(
        state["known_information"], updates["known_information"]
    )
    state["relationship_changes"] = _merge_strings(
        state["relationship_changes"], updates["relationship_changes"]
    )
    state["introduced_entities"] = _merge_entities(
        state["introduced_entities"], updates["introduced_entities"]
    )

    occurred_events = list(state["occurred_events"])
    completed_event_ids = set(updates["completed_event_ids"])
    for event in outline_section["key_events"]:
        if event["event_id"] not in completed_event_ids:
            continue
        event_record = {
            "chapter_no": chapter_no,
            "section_no": outline_section["section_no"],
            "subsection_no": subsection_no,
            "event_id": event["event_id"],
            "description": event["description"],
        }
        if event_record not in occurred_events:
            occurred_events.append(event_record)
    state["occurred_events"] = occurred_events

    resolved = set(updates["resolved_plot_threads"])
    unresolved = [
        thread
        for thread in state["unresolved_plot_threads"]
        if thread not in resolved
    ]
    state["unresolved_plot_threads"] = _merge_strings(
        unresolved, updates["unresolved_plot_threads"]
    )
    state["recent_context"] = updates["continuity_summary"]
    validate_scenario_state(state, expected_character_ids=character_ids)
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
        "current_subsection",
        "character_locations",
        "possessions",
        "known_information",
        "relationship_changes",
        "introduced_entities",
        "occurred_events",
        "unresolved_plot_threads",
        "recent_context",
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
        "introduced_entities",
        "occurred_events",
        "unresolved_plot_threads",
    ):
        if not isinstance(state[field], list):
            raise ValueError(f"Scenario {field} must be an array")
    if state["current_section"] is not None and not isinstance(
        state["current_section"], dict
    ):
        raise ValueError("Scenario current_section must be an object or null")
    if state["current_subsection"] is not None and (
        not isinstance(state["current_subsection"], int)
        or state["current_subsection"] <= 0
    ):
        raise ValueError("Scenario current_subsection must be a positive integer or null")
    if state["recent_context"] is not None and not isinstance(
        state["recent_context"], str
    ):
        raise ValueError("Scenario recent_context must be a string or null")


def _unique_strings(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _merge_strings(existing: list[str], updates: list[str]) -> list[str]:
    return _unique_strings([*existing, *updates])


def _merge_entities(
    existing: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {item["entity_id"]: deepcopy(item) for item in existing}
    for item in updates:
        by_id[item["entity_id"]] = deepcopy(item)
    return list(by_id.values())
