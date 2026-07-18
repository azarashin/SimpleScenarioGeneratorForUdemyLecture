from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.text_generation import (
    GenerationResponse,
    MockTextGenerationProvider,
    OpenAITextGenerationProvider,
    ScenarioBodyMockTextGenerationProvider,
    TextGenerationProvider,
    create_text_generation_provider,
    resolve_api_key,
)
from pipeline.engine import ExecutionOptions, StepExecutionEngine
from pipeline.steps import build_minimal_steps


class FailSecondSectionProvider(TextGenerationProvider):
    def __init__(self) -> None:
        self.delegate = ScenarioBodyMockTextGenerationProvider()
        self.calls = 0

    def generate_json(self, *, prompt: str, model: str, temperature: float):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("planned section failure")
        return self.delegate.generate_json(
            prompt=prompt,
            model=model,
            temperature=temperature,
        )


class FakeResponsesClient:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp-test",
            output_text=self.output_text,
            usage=SimpleNamespace(input_tokens=21, output_tokens=34),
        )


class UnderLengthOnceProvider(TextGenerationProvider):
    def __init__(self) -> None:
        self.delegate = ScenarioBodyMockTextGenerationProvider()
        self.calls = 0
        self.prompts: list[str] = []

    def generate_json(self, *, prompt: str, model: str, temperature: float):
        self.calls += 1
        self.prompts.append(prompt)
        response = self.delegate.generate_json(
            prompt=prompt,
            model=model,
            temperature=temperature,
        )
        if self.calls <= 2:
            for block in response.data["scenario_sections"][0]["narrative_blocks"]:
                block["text"] = "too short"
        return response


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


def test_api_key_is_read_from_named_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("SCENARIO_TEST_API_KEY", "secret-value")

    assert resolve_api_key("SCENARIO_TEST_API_KEY") == "secret-value"


def test_missing_api_key_fails_without_exposing_a_secret(monkeypatch) -> None:
    monkeypatch.delenv("SCENARIO_MISSING_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SCENARIO_MISSING_API_KEY"):
        resolve_api_key("SCENARIO_MISSING_API_KEY")


def test_scenario_sections_are_generated_and_checkpointed_individually(make_context) -> None:
    context, trace = make_context()
    provider = context.text_generation_provider

    output = StepExecutionEngine(build_minimal_steps()).run(context)

    assert len(output["scenario_sections"]) == 1
    assert len(provider.requests) == 1
    checkpoints = list((Path(context.artifacts_dir) / "sections").glob("*.json"))
    assert len(checkpoints) == 1
    assert sum(
        event.get("event") == "section_generated" for event in trace.events
    ) == 1


def test_section_retry_reuses_completed_checkpoint(make_context) -> None:
    context, trace = make_context()
    context.shared_data["input"]["scenario_idea"]["target_length"] = {
        "chapter_count": 1,
        "sections_per_chapter": 2,
    }
    provider = FailSecondSectionProvider()
    context.text_generation_provider = provider

    output = StepExecutionEngine(build_minimal_steps()).run(context)

    assert len(output["scenario_sections"]) == 2
    assert provider.calls == 3
    assert any(
        event.get("event") == "section_checkpoint_loaded"
        and event.get("section_no") == 1
        for event in trace.events
    )


def test_openai_provider_uses_responses_api_and_parses_json() -> None:
    responses = FakeResponsesClient('{"scenario_sections": []}')
    client = SimpleNamespace(responses=responses)
    provider = OpenAITextGenerationProvider(
        api_key="not-used-by-fake",
        timeout_seconds=30,
        client=client,
    )

    result = provider.generate_json(
        prompt="generate",
        model="gpt-test",
        temperature=0.2,
    )

    assert result.data == {"scenario_sections": []}
    assert result.input_tokens == 21
    assert result.output_tokens == 34
    assert result.provider_metadata["response_id"] == "resp-test"
    assert len(responses.calls) == 1
    assert responses.calls[0]["model"] == "gpt-test"
    assert responses.calls[0]["input"] == "generate"
    assert responses.calls[0]["temperature"] == 0.2
    assert responses.calls[0]["text"]["format"]["type"] == "json_schema"
    assert responses.calls[0]["text"]["format"]["strict"] is True


def test_openai_provider_rejects_non_json_output() -> None:
    provider = OpenAITextGenerationProvider(
        api_key="not-used-by-fake",
        timeout_seconds=30,
        client=SimpleNamespace(responses=FakeResponsesClient("not-json")),
    )

    with pytest.raises(ValueError, match="valid JSON"):
        provider.generate_json(prompt="p", model="m", temperature=0.2)


def test_invalid_section_is_retried_before_checkpoint_is_saved(make_context) -> None:
    context, trace = make_context()
    provider = UnderLengthOnceProvider()
    context.text_generation_provider = provider

    output = StepExecutionEngine(build_minimal_steps()).run(context)

    assert len(output["scenario_sections"]) == 1
    assert provider.calls == 3
    assert "RETRY CORRECTION" in provider.prompts[2]
    assert "body length must be 800-1600" in provider.prompts[2]
    assert sum(event.get("event") == "section_generated" for event in trace.events) == 1
    checkpoint = Path(context.artifacts_dir) / "sections" / "chapter-001-section-001.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    combined = "".join(block["text"] for block in payload["section"]["narrative_blocks"])
    assert sum(not character.isspace() for character in combined) >= 800


def test_invalid_existing_checkpoint_is_regenerated(make_context) -> None:
    context, _ = make_context()
    engine = StepExecutionEngine(build_minimal_steps())
    engine.run(context)
    checkpoint = Path(context.artifacts_dir) / "sections" / "chapter-001-section-001.json"
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    for block in payload["section"]["narrative_blocks"]:
        block["text"] = "invalid checkpoint"
    checkpoint.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    provider = ScenarioBodyMockTextGenerationProvider()
    context.text_generation_provider = provider

    output = engine.run(
        context,
        options=ExecutionOptions(from_step="step-03-generate-sections"),
    )

    assert len(output["scenario_sections"]) == 1
    assert len(provider.requests) == 1
