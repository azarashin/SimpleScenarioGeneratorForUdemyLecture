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
class RetryStrategyConfig:
    short_retries: int = 1
    prompt_revision_retries: int = 1
    fallback_enabled: bool = True


@dataclass(slots=True)
class AppConfig:
    model_name: str = "gpt-4.1-mini"
    temperature: float = 0.7
    output_root: str = "output"
    artifacts_dir_name: str = "artifacts"
    state_file_name: str = "run-state.json"
    trace_file_name: str = "trace.jsonl"
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)
    retry_strategy: RetryStrategyConfig = field(default_factory=RetryStrategyConfig)


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
        "output_root": DEFAULT_CONFIG.output_root,
        "artifacts_dir_name": DEFAULT_CONFIG.artifacts_dir_name,
        "state_file_name": DEFAULT_CONFIG.state_file_name,
        "trace_file_name": DEFAULT_CONFIG.trace_file_name,
        "retry_strategy": {
            "short_retries": DEFAULT_CONFIG.retry_strategy.short_retries,
            "prompt_revision_retries": DEFAULT_CONFIG.retry_strategy.prompt_revision_retries,
            "fallback_enabled": DEFAULT_CONFIG.retry_strategy.fallback_enabled,
        },
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
    retry_conf = merged.get("retry_strategy", {})
    short_retries = int(retry_conf.get("short_retries", 1))
    prompt_revision_retries = int(retry_conf.get("prompt_revision_retries", 1))
    if short_retries < 0 or prompt_revision_retries < 0:
        raise ValueError("Retry counts must be zero or greater.")
    return AppConfig(
        model_name=str(merged["model_name"]),
        temperature=float(merged["temperature"]),
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
        retry_strategy=RetryStrategyConfig(
            short_retries=short_retries,
            prompt_revision_retries=prompt_revision_retries,
            fallback_enabled=bool(retry_conf.get("fallback_enabled", True)),
        ),
    )
