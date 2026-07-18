from __future__ import annotations

import pytest

from pipeline.text_generation import (
    GenerationResponse,
    MockTextGenerationProvider,
    TextGenerationProvider,
    create_text_generation_provider,
)


def test_mock_provider_returns_queued_json_and_records_request() -> None:
    source = {"scenario_sections": [{"chapter_no": 1}]}
    provider = MockTextGenerationProvider([source])

    response = provider.generate_json(
        prompt="generate section",
        model="test-model",
        temperature=0.2,
    )

    assert response == GenerationResponse(data=source, model="test-model")
    assert provider.requests[0].prompt == "generate section"
    assert provider.requests[0].model == "test-model"
    assert provider.requests[0].temperature == 0.2

    response.data["scenario_sections"].append({"chapter_no": 2})
    assert source == {"scenario_sections": [{"chapter_no": 1}]}


def test_mock_provider_preserves_response_metadata() -> None:
    provider = MockTextGenerationProvider(
        [
            GenerationResponse(
                data={"ok": True},
                model="returned-model",
                input_tokens=12,
                output_tokens=34,
                provider_metadata={"request_id": "mock-1"},
            )
        ]
    )

    response = provider.generate_json(prompt="p", model="requested-model", temperature=0.7)

    assert response.model == "returned-model"
    assert response.input_tokens == 12
    assert response.output_tokens == 34
    assert response.provider_metadata == {"request_id": "mock-1"}


def test_mock_provider_fails_when_queue_is_empty() -> None:
    provider = MockTextGenerationProvider()

    with pytest.raises(RuntimeError, match="response queue is empty"):
        provider.generate_json(prompt="p", model="m", temperature=0.2)


def test_provider_factory_exposes_mock_and_rejects_unknown_provider() -> None:
    assert isinstance(create_text_generation_provider("mock"), TextGenerationProvider)

    with pytest.raises(ValueError, match="Unsupported text generation provider"):
        create_text_generation_provider("unknown")
