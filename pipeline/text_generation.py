from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
import os
import json
import re
from typing import Any


class LLMResponseFormatError(ValueError):
    """Raised when an LLM response is not exactly one JSON object."""


def extract_llm_json_text(raw_response: str) -> str:
    """Extract a bare JSON object text while rejecting any surrounding content."""
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise LLMResponseFormatError("LLM response is empty")

    stripped = raw_response.strip()
    if stripped.startswith("```") or stripped.endswith("```"):
        raise LLMResponseFormatError(
            "LLM response must be bare JSON without Markdown code fences"
        )
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise LLMResponseFormatError(
            "LLM response must contain only one JSON object without explanatory text"
        )
    return stripped


def parse_llm_json_object(raw_response: str) -> dict[str, Any]:
    """Extract and parse one JSON object, rejecting wrappers and duplicate keys."""
    json_text = extract_llm_json_text(raw_response)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LLMResponseFormatError(
                    f"LLM JSON response contains duplicate key: {key!r}"
                )
            result[key] = value
        return result

    try:
        data = json.loads(json_text, object_pairs_hook=reject_duplicate_keys)
    except LLMResponseFormatError:
        raise
    except json.JSONDecodeError as exc:
        raise LLMResponseFormatError(
            "LLM response must contain only one valid JSON object; "
            "prose and trailing content are not allowed"
        ) from exc
    if not isinstance(data, dict):
        raise LLMResponseFormatError("LLM JSON response must be an object")
    return data


@dataclass(frozen=True, slots=True)
class GenerationResponse:
    data: dict[str, Any]
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    prompt: str
    model: str
    temperature: float


class TextGenerationProvider(ABC):
    @abstractmethod
    def generate_json(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> GenerationResponse:
        """Generate and return a parsed JSON object."""
        raise NotImplementedError


class MockTextGenerationProvider(TextGenerationProvider):
    """Deterministic queued-response provider for local runs and tests."""

    def __init__(
        self,
        responses: Iterable[dict[str, Any] | GenerationResponse | Exception] = (),
    ) -> None:
        self._responses = list(responses)
        self.requests: list[GenerationRequest] = []

    def generate_json(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> GenerationResponse:
        self.requests.append(GenerationRequest(prompt, model, temperature))
        if not self._responses:
            raise RuntimeError("Mock text generation response queue is empty")

        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, GenerationResponse):
            return GenerationResponse(
                data=deepcopy(response.data),
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                provider_metadata=deepcopy(response.provider_metadata),
            )
        return GenerationResponse(data=deepcopy(response), model=model)


class ScenarioBodyMockTextGenerationProvider(TextGenerationProvider):
    """Deterministic local provider that derives a section from the rendered prompt."""

    target_marker = "TARGET SECTION\n"
    previous_state_marker = "PREVIOUS SECTION STATE OR SUMMARY\n"

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    def generate_json(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> GenerationResponse:
        self.requests.append(GenerationRequest(prompt, model, temperature))
        marker_index = prompt.find(self.target_marker)
        if marker_index < 0:
            raise ValueError("Scenario mock prompt does not contain TARGET SECTION")
        json_start = marker_index + len(self.target_marker)
        section, _ = json.JSONDecoder().raw_decode(prompt[json_start:])
        chapter_no = section.get("chapter_no")
        if chapter_no is None:
            # The chapter number is supplied in the separate chapter object.
            chapter_marker = "TARGET CHAPTER\n"
            chapter_start = prompt.index(chapter_marker) + len(chapter_marker)
            chapter, _ = json.JSONDecoder().raw_decode(prompt[chapter_start:])
            chapter_no = chapter["chapter_no"]
        section_no = section["section_no"]
        events = " / ".join(section["key_events"])
        purpose = section["section_purpose"]
        previous_start = prompt.index(self.previous_state_marker) + len(
            self.previous_state_marker
        )
        previous_state, _ = json.JSONDecoder().raw_decode(prompt[previous_start:])
        previous_section = previous_state.get("previous_section")
        previous_summary = "The story begins here."
        if isinstance(previous_section, dict):
            previous_summary = " ".join(
                block.get("text", "")
                for block in previous_section.get("narrative_blocks", [])
            )[:180]
        previous_events = " / ".join(
            str(item.get("event", ""))
            for item in previous_state.get("occurred_events", [])[-4:]
            if isinstance(item, dict)
        )
        carryover = (
            f" Previous events carried forward: {previous_events}."
            if previous_events
            else ""
        )
        narration = (
            f"Continuing from the established state ({previous_summary}), chapter "
            f"{chapter_no} section {section_no} advances this distinct purpose: {purpose}. "
            f"The scene explicitly develops the required events: {events}.{carryover} "
            "Characters observe the consequences, react according to their established "
            "roles, and move the situation forward without skipping causal steps. "
        )
        dialogue = (
            f"We carry the previous situation forward and confront these events now: {events}."
        )
        dialogue_match = re.search(
            r"Include narration and (\d+) to (\d+) dialogue blocks", prompt
        )
        dialogue_count = int(dialogue_match.group(1)) if dialogue_match else 1
        dialogue_blocks = [
            {
                "block_id": f"b-{chapter_no}-{section_no}-{index + 2}",
                "type": "dialogue",
                "text": dialogue if index == 0 else f"Turn {index + 1} advances.",
                "speaker_id": section["participating_characters"][
                    index % len(section["participating_characters"])
                ],
            }
            for index in range(dialogue_count)
        ]
        character_match = re.search(
            r"Produce (\d+) to (\d+) non-whitespace characters", prompt
        )
        min_characters = int(character_match.group(1)) if character_match else 3000
        max_characters = int(character_match.group(2)) if character_match else 3500
        dialogue_characters = sum(
            sum(not character.isspace() for character in block["text"])
            for block in dialogue_blocks
        )
        narration_characters = sum(not character.isspace() for character in narration)
        target_characters = min(max_characters, min_characters + 100)
        padding_needed = max(
            0, target_characters - dialogue_characters - narration_characters
        )
        if padding_needed:
            narration += " " + ("x" * padding_needed)
        payload = {
            "scenario_sections": [
                {
                    "chapter_no": chapter_no,
                    "section_no": section_no,
                    "section_title": section["section_title"],
                    "narrative_blocks": [
                        {
                            "block_id": f"b-{chapter_no}-{section_no}-1",
                            "type": "narration",
                            "text": narration,
                            "speaker_id": None,
                        },
                        *dialogue_blocks,
                    ],
                }
            ]
        }
        return GenerationResponse(
            data=payload,
            model=model,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(json.dumps(payload)) // 4),
            provider_metadata={"provider": "scenario-body-mock"},
        )


class OpenAITextGenerationProvider(TextGenerationProvider):
    """OpenAI Responses API implementation for real JSON generation."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The openai package is required for provider 'openai'"
                ) from exc
            client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self.client = client

    def generate_json(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> GenerationResponse:
        response = self.client.responses.create(
            model=model,
            input=prompt,
            temperature=temperature,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "scenario_section_response",
                    "strict": True,
                    "schema": _scenario_section_response_schema(),
                }
            },
        )
        data = parse_llm_json_object(response.output_text)
        usage = getattr(response, "usage", None)
        return GenerationResponse(
            data=data,
            model=model,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            provider_metadata={
                "provider": "openai",
                "response_id": getattr(response, "id", None),
            },
        )


def _scenario_section_response_schema() -> dict[str, Any]:
    block_schema = {
        "type": "object",
        "required": ["block_id", "type", "text", "speaker_id"],
        "additionalProperties": False,
        "properties": {
            "block_id": {"type": "string", "minLength": 1},
            "type": {"type": "string", "enum": ["narration", "dialogue"]},
            "text": {"type": "string", "minLength": 1},
            "speaker_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
            },
        },
    }
    section_schema = {
        "type": "object",
        "required": ["chapter_no", "section_no", "section_title", "narrative_blocks"],
        "additionalProperties": False,
        "properties": {
            "chapter_no": {"type": "integer", "minimum": 1},
            "section_no": {"type": "integer", "minimum": 1},
            "section_title": {"type": "string", "minLength": 1},
            "narrative_blocks": {
                "type": "array",
                "minItems": 2,
                "items": block_schema,
            },
        },
    }
    return {
        "type": "object",
        "required": ["scenario_sections"],
        "additionalProperties": False,
        "properties": {
            "scenario_sections": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": section_schema,
            }
        },
    }


def resolve_api_key(environment_variable: str) -> str:
    value = os.environ.get(environment_variable, "").strip()
    if not value:
        raise RuntimeError(
            f"Text generation API key environment variable is not set: "
            f"{environment_variable}"
        )
    return value


def create_text_generation_provider(
    provider_name: str,
    *,
    timeout_seconds: float = 60.0,
    api_key_env: str = "TEXT_GENERATION_API_KEY",
) -> TextGenerationProvider:
    if timeout_seconds <= 0:
        raise ValueError("Text generation timeout must be greater than zero")
    if provider_name == "mock":
        return ScenarioBodyMockTextGenerationProvider()
    if provider_name == "openai":
        return OpenAITextGenerationProvider(
            api_key=resolve_api_key(api_key_env),
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"Unsupported text generation provider: {provider_name}")
