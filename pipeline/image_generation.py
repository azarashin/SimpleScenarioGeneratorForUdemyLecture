from __future__ import annotations

from abc import ABC, abstractmethod
import binascii
from dataclasses import dataclass, field
import hashlib
import struct
from typing import Any
import zlib


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    prompt: str
    model: str
    width: int
    height: int
    style_preset: str


@dataclass(frozen=True, slots=True)
class ImageGenerationResponse:
    image_bytes: bytes
    mime_type: str
    model: str
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class ImageGenerationProvider(ABC):
    @abstractmethod
    def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        width: int,
        height: int,
        style_preset: str,
    ) -> ImageGenerationResponse:
        """Generate one raster image and return its encoded bytes."""
        raise NotImplementedError


class MockImageGenerationProvider(ImageGenerationProvider):
    """Dependency-free deterministic image provider for local runs and tests."""

    def __init__(self) -> None:
        self.requests: list[ImageGenerationRequest] = []

    def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        width: int,
        height: int,
        style_preset: str,
    ) -> ImageGenerationResponse:
        _validate_request(prompt=prompt, width=width, height=height)
        request = ImageGenerationRequest(
            prompt=prompt,
            model=model,
            width=width,
            height=height,
            style_preset=style_preset,
        )
        self.requests.append(request)
        request_hash = hashlib.sha256(
            f"{prompt}\0{model}\0{width}x{height}\0{style_preset}".encode("utf-8")
        ).hexdigest()
        return ImageGenerationResponse(
            image_bytes=_transparent_png(width, height),
            mime_type="image/png",
            model=model,
            provider_metadata={
                "provider": "mock",
                "request_hash": request_hash,
                "requested_width": width,
                "requested_height": height,
            },
        )


def _transparent_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return (
            struct.pack(">I", len(data))
            + payload
            + struct.pack(">I", binascii.crc32(payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    transparent_row = b"\x00" + (b"\x00\x00\x00\x00" * width)
    pixels = zlib.compress(transparent_row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def _validate_request(*, prompt: str, width: int, height: int) -> None:
    if not prompt.strip():
        raise ValueError("Image generation prompt must not be empty")
    if width <= 0 or height <= 0:
        raise ValueError("Image generation dimensions must be greater than zero")


def create_image_generation_provider(provider_name: str) -> ImageGenerationProvider:
    normalized_name = provider_name.strip().casefold()
    if normalized_name == "mock":
        return MockImageGenerationProvider()
    raise ValueError(f"Unsupported image generation provider: {provider_name}")
