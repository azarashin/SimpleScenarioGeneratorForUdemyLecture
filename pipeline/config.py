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
class TextGenerationConfig:
    provider: str = "mock"
    model: str = "gpt-4.1-mini"
    timeout_seconds: float = 60.0
    api_key_env: str = "TEXT_GENERATION_API_KEY"


@dataclass(slots=True)
class ScenarioBodyGenerationConfig:
    min_characters: int = 800
    max_characters: int = 1600
    min_dialogue_blocks: int = 20
    max_dialogue_blocks: int = 40
    require_event_mentions: bool = True


@dataclass(slots=True)
class RetryStrategyConfig:
    short_retries: int = 1
    prompt_revision_retries: int = 1
    fallback_enabled: bool = True


@dataclass(slots=True)
class TemperaturePolicyConfig:
    low_temperature: float = 0.2
    diversity_temperature: float = 0.7
    diversity_steps: tuple[str, ...] = (
        "step-02-generate-outline",
        "step-03-generate-sections",
    )


@dataclass(slots=True)
class AppConfig:
    output_root: str = "output"
    artifacts_dir_name: str = "artifacts"
    state_file_name: str = "run-state.json"
    trace_file_name: str = "trace.jsonl"
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)
    text_generation: TextGenerationConfig = field(default_factory=TextGenerationConfig)
    scenario_body_generation: ScenarioBodyGenerationConfig = field(
        default_factory=ScenarioBodyGenerationConfig
    )
    retry_strategy: RetryStrategyConfig = field(default_factory=RetryStrategyConfig)
    temperature_policy: TemperaturePolicyConfig = field(
        default_factory=TemperaturePolicyConfig
    )
    prompt_versions: dict[str, str] = field(default_factory=dict)

    @property
    def model_name(self) -> str:
        """Compatibility alias for code that consumes the configured text model."""
        return self.text_generation.model

    def temperature_for(self, step_name: str) -> float:
        if step_name in self.temperature_policy.diversity_steps:
            return self.temperature_policy.diversity_temperature
        return self.temperature_policy.low_temperature


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
        "output_root": DEFAULT_CONFIG.output_root,
        "artifacts_dir_name": DEFAULT_CONFIG.artifacts_dir_name,
        "state_file_name": DEFAULT_CONFIG.state_file_name,
        "trace_file_name": DEFAULT_CONFIG.trace_file_name,
        "retry_strategy": {
            "short_retries": DEFAULT_CONFIG.retry_strategy.short_retries,
            "prompt_revision_retries": DEFAULT_CONFIG.retry_strategy.prompt_revision_retries,
            "fallback_enabled": DEFAULT_CONFIG.retry_strategy.fallback_enabled,
        },
        "temperature_policy": {
            "low_temperature": DEFAULT_CONFIG.temperature_policy.low_temperature,
            "diversity_temperature": DEFAULT_CONFIG.temperature_policy.diversity_temperature,
            "diversity_steps": list(DEFAULT_CONFIG.temperature_policy.diversity_steps),
        },
        "prompt_versions": dict(DEFAULT_CONFIG.prompt_versions),
        "text_generation": {
            "provider": DEFAULT_CONFIG.text_generation.provider,
            "model": DEFAULT_CONFIG.text_generation.model,
            "timeout_seconds": DEFAULT_CONFIG.text_generation.timeout_seconds,
            "api_key_env": DEFAULT_CONFIG.text_generation.api_key_env,
        },
        "scenario_body_generation": {
            "min_characters": DEFAULT_CONFIG.scenario_body_generation.min_characters,
            "max_characters": DEFAULT_CONFIG.scenario_body_generation.max_characters,
            "min_dialogue_blocks": DEFAULT_CONFIG.scenario_body_generation.min_dialogue_blocks,
            "max_dialogue_blocks": DEFAULT_CONFIG.scenario_body_generation.max_dialogue_blocks,
            "require_event_mentions": (
                DEFAULT_CONFIG.scenario_body_generation.require_event_mentions
            ),
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
    text_conf = merged.get("text_generation", {})
    body_conf = merged.get("scenario_body_generation", {})
    retry_conf = merged.get("retry_strategy", {})
    temperature_conf = merged.get("temperature_policy", {})
    short_retries = int(retry_conf.get("short_retries", 1))
    prompt_revision_retries = int(retry_conf.get("prompt_revision_retries", 1))
    if short_retries < 0 or prompt_revision_retries < 0:
        raise ValueError("Retry counts must be zero or greater.")
    low_temperature = float(temperature_conf.get("low_temperature", 0.2))
    diversity_temperature = float(temperature_conf.get("diversity_temperature", 0.7))
    if not 0 <= low_temperature <= 2 or not 0 <= diversity_temperature <= 2:
        raise ValueError("Temperatures must be between 0 and 2.")
    if low_temperature > diversity_temperature:
        raise ValueError("Low temperature cannot exceed diversity temperature.")
    diversity_steps = tuple(str(item) for item in temperature_conf.get("diversity_steps", ()))
    text_provider = str(text_conf.get("provider", "")).strip()
    text_model = str(text_conf.get("model", "")).strip()
    api_key_env = str(text_conf.get("api_key_env", "")).strip()
    timeout_seconds = float(text_conf.get("timeout_seconds", 60))
    if not text_provider or not text_model or not api_key_env:
        raise ValueError("Text generation provider, model, and api_key_env are required.")
    if timeout_seconds <= 0:
        raise ValueError("Text generation timeout_seconds must be greater than zero.")
    min_characters = int(body_conf.get("min_characters", 800))
    max_characters = int(body_conf.get("max_characters", 1600))
    min_dialogue_blocks = int(body_conf.get("min_dialogue_blocks", 20))
    max_dialogue_blocks = int(body_conf.get("max_dialogue_blocks", 40))
    if min_characters <= 0 or max_characters < min_characters:
        raise ValueError("Scenario body character limits are invalid.")
    if min_dialogue_blocks <= 0 or max_dialogue_blocks < min_dialogue_blocks:
        raise ValueError("Scenario body dialogue block limits are invalid.")
    return AppConfig(
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
        text_generation=TextGenerationConfig(
            provider=text_provider,
            model=text_model,
            timeout_seconds=timeout_seconds,
            api_key_env=api_key_env,
        ),
        scenario_body_generation=ScenarioBodyGenerationConfig(
            min_characters=min_characters,
            max_characters=max_characters,
            min_dialogue_blocks=min_dialogue_blocks,
            max_dialogue_blocks=max_dialogue_blocks,
            require_event_mentions=bool(body_conf.get("require_event_mentions", True)),
        ),
        retry_strategy=RetryStrategyConfig(
            short_retries=short_retries,
            prompt_revision_retries=prompt_revision_retries,
            fallback_enabled=bool(retry_conf.get("fallback_enabled", True)),
        ),
        temperature_policy=TemperaturePolicyConfig(
            low_temperature=low_temperature,
            diversity_temperature=diversity_temperature,
            diversity_steps=diversity_steps,
        ),
        prompt_versions={
            str(step): str(version)
            for step, version in merged.get("prompt_versions", {}).items()
        },
    )
