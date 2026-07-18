from __future__ import annotations

import struct

import pytest

from pipeline.image_generation import (
    ImageGenerationProvider,
    MockImageGenerationProvider,
    create_image_generation_provider,
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
