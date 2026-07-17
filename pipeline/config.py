from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ImageGenerationConfig:
    provider: str = "mock"
    model: str = "image-mock-v1"
    width: int = 1024
    height: int = 1024
    style_preset: str = "anime"


@dataclass(slots=True)
class AppConfig:
    model_name: str = "gpt-4.1-mini"
    temperature: float = 0.7
    max_retries: int = 2
    output_root: str = "output"
    artifacts_dir_name: str = "artifacts"
    state_file_name: str = "run-state.json"
    trace_file_name: str = "trace.jsonl"
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)


DEFAULT_CONFIG = AppConfig()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _to_default_dict() -> dict[str, Any]:
    return {
        "model_name": DEFAULT_CONFIG.model_name,
        "temperature": DEFAULT_CONFIG.temperature,
        "max_retries": DEFAULT_CONFIG.max_retries,
        "output_root": DEFAULT_CONFIG.output_root,
        "artifacts_dir_name": DEFAULT_CONFIG.artifacts_dir_name,
        "state_file_name": DEFAULT_CONFIG.state_file_name,
        "trace_file_name": DEFAULT_CONFIG.trace_file_name,
        "image_generation": {
            "provider": DEFAULT_CONFIG.image_generation.provider,
            "model": DEFAULT_CONFIG.image_generation.model,
            "width": DEFAULT_CONFIG.image_generation.width,
            "height": DEFAULT_CONFIG.image_generation.height,
            "style_preset": DEFAULT_CONFIG.image_generation.style_preset,
        },
    }


def load_config(config_path: str | None) -> AppConfig:
    merged = _to_default_dict()
    if config_path:
        text = Path(config_path).read_text(encoding="utf-8")
        user_config = json.loads(text)
        if not isinstance(user_config, dict):
            raise ValueError("Config file must be a JSON object.")
        merged = _deep_merge(merged, user_config)

    image_conf = merged.get("image_generation", {})
    return AppConfig(
        model_name=str(merged["model_name"]),
        temperature=float(merged["temperature"]),
        max_retries=int(merged["max_retries"]),
        output_root=str(merged["output_root"]),
        artifacts_dir_name=str(merged["artifacts_dir_name"]),
        state_file_name=str(merged["state_file_name"]),
        trace_file_name=str(merged["trace_file_name"]),
        image_generation=ImageGenerationConfig(
            provider=str(image_conf.get("provider", DEFAULT_CONFIG.image_generation.provider)),
            model=str(image_conf.get("model", DEFAULT_CONFIG.image_generation.model)),
            width=int(image_conf.get("width", DEFAULT_CONFIG.image_generation.width)),
            height=int(image_conf.get("height", DEFAULT_CONFIG.image_generation.height)),
            style_preset=str(image_conf.get("style_preset", DEFAULT_CONFIG.image_generation.style_preset)),
        ),
    )
