from __future__ import annotations

import base64
import struct
from types import SimpleNamespace

import pytest

from pipeline.image_generation import (
    ImageGenerationProvider,
    MockImageGenerationProvider,
    OpenAIImageGenerationProvider,
    create_image_generation_provider,
    resolve_image_api_key,
)


class FakeImagesClient:
    def __init__(self, image_bytes: bytes) -> None:
        self.images = self
        self.image_bytes = image_bytes
        self.generate_calls = []
        self.edit_calls = []

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return self._response()

    def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        return self._response()

    def _response(self):
        return SimpleNamespace(
            _request_id="image-request-1",
            created=123,
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(self.image_bytes).decode("ascii")
                )
            ],
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
            ),
        )


def test_mock_provider_returns_png_and_records_request() -> None:
    provider = MockImageGenerationProvider()

    response = provider.generate_image(
        prompt="A character portrait",
        model="chat-gpt-image-2",
        width=1024,
        height=1024,
        style_preset="anime",
    )

    assert response.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.mime_type == "image/png"
    assert response.model == "chat-gpt-image-2"
    assert response.provider_metadata["provider"] == "mock"
    assert struct.unpack(">II", response.image_bytes[16:24]) == (1024, 1024)
    assert provider.requests[0].prompt == "A character portrait"
    assert provider.requests[0].width == 1024
    assert provider.requests[0].height == 1024
    assert provider.requests[0].style_preset == "anime"


def test_mock_provider_is_deterministic() -> None:
    provider = MockImageGenerationProvider()
    arguments = {
        "prompt": "A character portrait",
        "model": "chat-gpt-image-2",
        "width": 1024,
        "height": 1024,
        "style_preset": "anime",
    }

    first = provider.generate_image(**arguments)
    second = provider.generate_image(**arguments)

    assert first.image_bytes == second.image_bytes
    assert (
        first.provider_metadata["request_hash"]
        == second.provider_metadata["request_hash"]
    )


@pytest.mark.parametrize(
    ("prompt", "width", "height", "message"),
    [
        (" ", 1024, 1024, "prompt must not be empty"),
        ("portrait", 0, 1024, "dimensions must be greater than zero"),
        ("portrait", 1024, -1, "dimensions must be greater than zero"),
    ],
)
def test_mock_provider_rejects_invalid_requests(
    prompt: str, width: int, height: int, message: str
) -> None:
    provider = MockImageGenerationProvider()

    with pytest.raises(ValueError, match=message):
        provider.generate_image(
            prompt=prompt,
            model="chat-gpt-image-2",
            width=width,
            height=height,
            style_preset="anime",
        )

    assert provider.requests == []


def test_provider_factory_exposes_mock_and_rejects_unknown_provider() -> None:
    assert isinstance(
        create_image_generation_provider("mock"), ImageGenerationProvider
    )
    assert isinstance(
        create_image_generation_provider(" Mock "), MockImageGenerationProvider
    )

    with pytest.raises(ValueError, match="Unsupported image generation provider"):
        create_image_generation_provider("unknown")


def test_openai_provider_generates_high_quality_png_and_maps_model_alias() -> None:
    image_bytes = MockImageGenerationProvider().generate_image(
        prompt="fixture",
        model="mock",
        width=1024,
        height=1024,
        style_preset="anime",
    ).image_bytes
    client = FakeImagesClient(image_bytes)
    provider = OpenAIImageGenerationProvider(
        api_key="test-key",
        timeout_seconds=120,
        quality="high",
        output_format="png",
        client=client,
    )

    response = provider.generate_image(
        prompt="A character portrait",
        model="chat-gpt-image-2",
        width=1024,
        height=1024,
        style_preset="anime",
    )

    assert response.image_bytes == image_bytes
    assert response.model == "gpt-image-2"
    assert response.mime_type == "image/png"
    assert response.provider_metadata["request_id"] == "image-request-1"
    assert response.provider_metadata["configured_model"] == "chat-gpt-image-2"
    assert client.generate_calls == [
        {
            "model": "gpt-image-2",
            "prompt": "A character portrait",
            "n": 1,
            "size": "1024x1024",
            "quality": "high",
            "output_format": "png",
        }
    ]


def test_openai_provider_uses_edit_api_for_expression_reference() -> None:
    image_bytes = MockImageGenerationProvider().generate_image(
        prompt="fixture",
        model="mock",
        width=1024,
        height=1024,
        style_preset="anime",
    ).image_bytes
    client = FakeImagesClient(image_bytes)
    provider = OpenAIImageGenerationProvider(
        api_key="test-key",
        timeout_seconds=120,
        client=client,
    )

    response = provider.generate_image(
        prompt="Make the expression happy",
        model="gpt-image-2",
        width=1024,
        height=1024,
        style_preset="anime",
        reference_image_bytes=image_bytes,
        reference_mime_type="image/png",
    )

    assert response.provider_metadata["operation"] == "edit"
    assert client.generate_calls == []
    assert len(client.edit_calls) == 1
    assert client.edit_calls[0]["image"].name == "reference.png"


def test_openai_provider_rejects_unsupported_gpt_image_2_size() -> None:
    provider = OpenAIImageGenerationProvider(
        api_key="test-key",
        timeout_seconds=120,
        client=FakeImagesClient(b"unused"),
    )

    with pytest.raises(ValueError, match="multiples of 16"):
        provider.generate_image(
            prompt="portrait",
            model="gpt-image-2",
            width=1025,
            height=1024,
            style_preset="anime",
        )


def test_image_api_key_is_resolved_from_named_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_TEST_API_KEY", "secret-value")
    assert resolve_image_api_key("IMAGE_TEST_API_KEY") == "secret-value"


def test_missing_image_api_key_does_not_expose_a_secret(monkeypatch) -> None:
    monkeypatch.delenv("IMAGE_MISSING_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="IMAGE_MISSING_API_KEY"):
        resolve_image_api_key("IMAGE_MISSING_API_KEY")
