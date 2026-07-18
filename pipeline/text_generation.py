from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
import os
from typing import Any


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

    def __init__(self, responses: Iterable[dict[str, Any] | GenerationResponse] = ()) -> None:
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
        if isinstance(response, GenerationResponse):
            return GenerationResponse(
                data=deepcopy(response.data),
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                provider_metadata=deepcopy(response.provider_metadata),
            )
        return GenerationResponse(data=deepcopy(response), model=model)


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
        return MockTextGenerationProvider()
    # Future network-backed implementations must call resolve_api_key(api_key_env)
    # here and pass the secret directly to their client without persisting it.
    raise ValueError(f"Unsupported text generation provider: {provider_name}")
