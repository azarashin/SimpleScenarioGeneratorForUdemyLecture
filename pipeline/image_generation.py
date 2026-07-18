from __future__ import annotations

from abc import ABC, abstractmethod
import base64
import binascii
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import os
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
    reference_image_hash: str | None = None


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
        reference_image_bytes: bytes | None = None,
        reference_mime_type: str | None = None,
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
        reference_image_bytes: bytes | None = None,
        reference_mime_type: str | None = None,
    ) -> ImageGenerationResponse:
        _validate_request(prompt=prompt, width=width, height=height)
        request = ImageGenerationRequest(
            prompt=prompt,
            model=model,
            width=width,
            height=height,
            style_preset=style_preset,
            reference_image_hash=(
                hashlib.sha256(reference_image_bytes).hexdigest()
                if reference_image_bytes is not None
                else None
            ),
        )
        self.requests.append(request)
        request_hash = hashlib.sha256(
            (
                f"{prompt}\0{model}\0{width}x{height}\0{style_preset}\0"
                f"{request.reference_image_hash or ''}"
            ).encode("utf-8")
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


class OpenAIImageGenerationProvider(ImageGenerationProvider):
    """OpenAI Image API provider for GPT Image models."""

    model_aliases = {"chat-gpt-image-2": "gpt-image-2"}
    mime_types = {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        quality: str = "high",
        output_format: str = "png",
        client: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Image generation timeout must be greater than zero")
        if quality not in {"low", "medium", "high", "auto"}:
            raise ValueError("Unsupported OpenAI image quality")
        if output_format not in self.mime_types:
            raise ValueError("Unsupported OpenAI image output format")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The openai package is required for image provider 'openai'"
                ) from exc
            client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self.client = client
        self.quality = quality
        self.output_format = output_format

    def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        width: int,
        height: int,
        style_preset: str,
        reference_image_bytes: bytes | None = None,
        reference_mime_type: str | None = None,
    ) -> ImageGenerationResponse:
        _validate_request(prompt=prompt, width=width, height=height)
        api_model = self.model_aliases.get(model, model)
        if api_model == "gpt-image-2":
            _validate_gpt_image_2_size(width, height)
        request = {
            "model": api_model,
            "prompt": prompt,
            "n": 1,
            "size": f"{width}x{height}",
            "quality": self.quality,
            "output_format": self.output_format,
        }
        if reference_image_bytes is None:
            response = self.client.images.generate(**request)
        else:
            if not reference_image_bytes:
                raise ValueError("Reference image must not be empty")
            reference_file = BytesIO(reference_image_bytes)
            reference_file.name = self._reference_filename(reference_mime_type)
            try:
                response = self.client.images.edit(image=reference_file, **request)
            finally:
                reference_file.close()
        data = getattr(response, "data", None)
        encoded_image = getattr(data[0], "b64_json", None) if data else None
        if not isinstance(encoded_image, str) or not encoded_image:
            raise RuntimeError("OpenAI image response did not contain base64 image data")
        try:
            image_bytes = base64.b64decode(encoded_image, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("OpenAI image response contained invalid base64 data") from exc
        if not image_bytes:
            raise RuntimeError("OpenAI image response decoded to empty content")
        usage = getattr(response, "usage", None)
        return ImageGenerationResponse(
            image_bytes=image_bytes,
            mime_type=self.mime_types[self.output_format],
            model=api_model,
            provider_metadata={
                "provider": "openai",
                "request_id": getattr(response, "_request_id", None),
                "created": getattr(response, "created", None),
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "configured_model": model,
                "style_preset": style_preset,
                "operation": (
                    "edit" if reference_image_bytes is not None else "generate"
                ),
            },
        )

    @staticmethod
    def _reference_filename(mime_type: str | None) -> str:
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }.get((mime_type or "").casefold())
        if extension is None:
            raise ValueError("Reference image MIME type must be PNG, JPEG, or WebP")
        return f"reference.{extension}"


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


def _validate_gpt_image_2_size(width: int, height: int) -> None:
    long_edge = max(width, height)
    short_edge = min(width, height)
    pixels = width * height
    if long_edge > 3840:
        raise ValueError("gpt-image-2 image edges must not exceed 3840 pixels")
    if width % 16 or height % 16:
        raise ValueError("gpt-image-2 image edges must be multiples of 16 pixels")
    if long_edge > short_edge * 3:
        raise ValueError("gpt-image-2 image aspect ratio must not exceed 3:1")
    if not 655_360 <= pixels <= 8_294_400:
        raise ValueError(
            "gpt-image-2 total pixels must be between 655360 and 8294400"
        )


def resolve_image_api_key(environment_variable: str) -> str:
    value = os.environ.get(environment_variable, "").strip()
    if not value:
        raise RuntimeError(
            "Image generation API key environment variable is not set: "
            f"{environment_variable}"
        )
    return value


def create_image_generation_provider(
    provider_name: str,
    *,
    quality: str = "high",
    output_format: str = "png",
    timeout_seconds: float = 120.0,
    api_key_env: str = "OPENAI_API_KEY",
) -> ImageGenerationProvider:
    normalized_name = provider_name.strip().casefold()
    if normalized_name == "mock":
        return MockImageGenerationProvider()
    if normalized_name == "openai":
        return OpenAIImageGenerationProvider(
            api_key=resolve_image_api_key(api_key_env),
            timeout_seconds=timeout_seconds,
            quality=quality,
            output_format=output_format,
        )
    raise ValueError(f"Unsupported image generation provider: {provider_name}")
