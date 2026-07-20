from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ImageGenerationConfig:
    provider: str = "mock"
    model: str = "chat-gpt-image-2"
    width: int = 1024
    height: int = 1024
    expression_sheet_width: int = 2048
    expression_sheet_height: int = 2048
    style_preset: str = "anime"
    quality: str = "high"
    output_format: str = "png"
    timeout_seconds: float = 120.0
    api_key_env: str = "OPENAI_API_KEY"


@dataclass(slots=True)
class TextGenerationConfig:
    provider: str = "mock"
    model: str = "gpt-4.1-mini"
    timeout_seconds: float = 60.0
    api_key_env: str = "TEXT_GENERATION_API_KEY"


@dataclass(slots=True)
class CharacterProfileGenerationConfig:
    enabled: bool = False
    require_review: bool = True


@dataclass(slots=True)
class PlanningInputGenerationConfig:
    enabled: bool = False
    require_review: bool = True


@dataclass(slots=True)
class ScenarioBodyGenerationConfig:
    subsections_per_section: int = 3
    target_characters: int = 1200
    min_characters: int = 1000
    max_characters: int = 1600
    min_dialogue_blocks: int = 6
    max_dialogue_blocks: int = 14
    require_event_mentions: bool = True


@dataclass(slots=True)
class ScenarioReviewConfig:
    enabled: bool = False
    require_human_review: bool = False


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
        "step-04-generate-sections",
    )


@dataclass(slots=True)
class AppConfig:
    output_root: str = "output"
    artifacts_dir_name: str = "artifacts"
    state_file_name: str = "run-state.json"
    trace_file_name: str = "trace.jsonl"
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)
    text_generation: TextGenerationConfig = field(default_factory=TextGenerationConfig)
    character_profile_generation: CharacterProfileGenerationConfig = field(
        default_factory=CharacterProfileGenerationConfig
    )
    planning_input_generation: PlanningInputGenerationConfig = field(
        default_factory=PlanningInputGenerationConfig
    )
    scenario_body_generation: ScenarioBodyGenerationConfig = field(
        default_factory=ScenarioBodyGenerationConfig
    )
    scenario_review: ScenarioReviewConfig = field(default_factory=ScenarioReviewConfig)
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
        "character_profile_generation": {
            "enabled": DEFAULT_CONFIG.character_profile_generation.enabled,
            "require_review": DEFAULT_CONFIG.character_profile_generation.require_review,
        },
        "planning_input_generation": {
            "enabled": DEFAULT_CONFIG.planning_input_generation.enabled,
            "require_review": DEFAULT_CONFIG.planning_input_generation.require_review,
        },
        "scenario_body_generation": {
            "subsections_per_section": (
                DEFAULT_CONFIG.scenario_body_generation.subsections_per_section
            ),
            "target_characters": DEFAULT_CONFIG.scenario_body_generation.target_characters,
            "min_characters": DEFAULT_CONFIG.scenario_body_generation.min_characters,
            "max_characters": DEFAULT_CONFIG.scenario_body_generation.max_characters,
            "min_dialogue_blocks": DEFAULT_CONFIG.scenario_body_generation.min_dialogue_blocks,
            "max_dialogue_blocks": DEFAULT_CONFIG.scenario_body_generation.max_dialogue_blocks,
            "require_event_mentions": (
                DEFAULT_CONFIG.scenario_body_generation.require_event_mentions
            ),
        },
        "scenario_review": {
            "enabled": DEFAULT_CONFIG.scenario_review.enabled,
            "require_human_review": DEFAULT_CONFIG.scenario_review.require_human_review,
        },
        "image_generation": {
            "provider": DEFAULT_CONFIG.image_generation.provider,
            "model": DEFAULT_CONFIG.image_generation.model,
            "width": DEFAULT_CONFIG.image_generation.width,
            "height": DEFAULT_CONFIG.image_generation.height,
            "expression_sheet_width": (
                DEFAULT_CONFIG.image_generation.expression_sheet_width
            ),
            "expression_sheet_height": (
                DEFAULT_CONFIG.image_generation.expression_sheet_height
            ),
            "style_preset": DEFAULT_CONFIG.image_generation.style_preset,
            "quality": DEFAULT_CONFIG.image_generation.quality,
            "output_format": DEFAULT_CONFIG.image_generation.output_format,
            "timeout_seconds": DEFAULT_CONFIG.image_generation.timeout_seconds,
            "api_key_env": DEFAULT_CONFIG.image_generation.api_key_env,
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
    profile_generation_conf = merged.get("character_profile_generation", {})
    planning_generation_conf = merged.get("planning_input_generation", {})
    review_conf = merged.get("scenario_review", {})
    retry_conf = merged.get("retry_strategy", {})
    temperature_conf = merged.get("temperature_policy", {})
    image_provider = str(image_conf.get("provider", "")).strip()
    image_model = str(image_conf.get("model", "")).strip()
    image_width = int(image_conf.get("width", 1024))
    image_height = int(image_conf.get("height", 1024))
    expression_sheet_width = int(image_conf.get("expression_sheet_width", 2048))
    expression_sheet_height = int(image_conf.get("expression_sheet_height", 2048))
    image_style = str(image_conf.get("style_preset", "")).strip()
    image_quality = str(image_conf.get("quality", "high")).strip().casefold()
    image_output_format = str(image_conf.get("output_format", "png")).strip().casefold()
    image_timeout = float(image_conf.get("timeout_seconds", 120))
    image_api_key_env = str(image_conf.get("api_key_env", "")).strip()
    if not image_provider or not image_model or not image_style or not image_api_key_env:
        raise ValueError(
            "Image generation provider, model, style_preset, and api_key_env are required."
        )
    if (
        image_width <= 0
        or image_height <= 0
        or expression_sheet_width <= 0
        or expression_sheet_height <= 0
        or image_timeout <= 0
    ):
        raise ValueError("Image generation dimensions and timeout must be greater than zero.")
    if expression_sheet_width % 4 or expression_sheet_height % 4:
        raise ValueError("Expression sheet dimensions must be divisible by 4.")
    if image_quality not in {"low", "medium", "high", "auto"}:
        raise ValueError("Image generation quality must be low, medium, high, or auto.")
    if image_output_format not in {"png", "jpeg", "webp"}:
        raise ValueError("Image generation output_format must be png, jpeg, or webp.")
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
    target_characters = int(
        body_conf.get(
            "target_characters",
            DEFAULT_CONFIG.scenario_body_generation.target_characters,
        )
    )
    min_characters = int(
        body_conf.get(
            "min_characters", DEFAULT_CONFIG.scenario_body_generation.min_characters
        )
    )
    max_characters = int(
        body_conf.get(
            "max_characters", DEFAULT_CONFIG.scenario_body_generation.max_characters
        )
    )
    subsections_per_section = int(
        body_conf.get(
            "subsections_per_section",
            DEFAULT_CONFIG.scenario_body_generation.subsections_per_section,
        )
    )
    min_dialogue_blocks = int(
        body_conf.get(
            "min_dialogue_blocks",
            DEFAULT_CONFIG.scenario_body_generation.min_dialogue_blocks,
        )
    )
    max_dialogue_blocks = int(
        body_conf.get(
            "max_dialogue_blocks",
            DEFAULT_CONFIG.scenario_body_generation.max_dialogue_blocks,
        )
    )
    if (
        subsections_per_section <= 0
        or min_characters <= 0
        or max_characters < min_characters
        or not min_characters <= target_characters <= max_characters
    ):
        raise ValueError("Scenario body character limits are invalid.")
    if min_dialogue_blocks <= 0 or max_dialogue_blocks < min_dialogue_blocks:
        raise ValueError("Scenario body dialogue block limits are invalid.")
    return AppConfig(
        output_root=str(merged["output_root"]),
        artifacts_dir_name=str(merged["artifacts_dir_name"]),
        state_file_name=str(merged["state_file_name"]),
        trace_file_name=str(merged["trace_file_name"]),
        image_generation=ImageGenerationConfig(
            provider=image_provider,
            model=image_model,
            width=image_width,
            height=image_height,
            expression_sheet_width=expression_sheet_width,
            expression_sheet_height=expression_sheet_height,
            style_preset=image_style,
            quality=image_quality,
            output_format=image_output_format,
            timeout_seconds=image_timeout,
            api_key_env=image_api_key_env,
        ),
        text_generation=TextGenerationConfig(
            provider=text_provider,
            model=text_model,
            timeout_seconds=timeout_seconds,
            api_key_env=api_key_env,
        ),
        character_profile_generation=CharacterProfileGenerationConfig(
            enabled=bool(profile_generation_conf.get("enabled", False)),
            require_review=bool(profile_generation_conf.get("require_review", True)),
        ),
        planning_input_generation=PlanningInputGenerationConfig(
            enabled=bool(planning_generation_conf.get("enabled", False)),
            require_review=bool(planning_generation_conf.get("require_review", True)),
        ),
        scenario_body_generation=ScenarioBodyGenerationConfig(
            subsections_per_section=subsections_per_section,
            target_characters=target_characters,
            min_characters=min_characters,
            max_characters=max_characters,
            min_dialogue_blocks=min_dialogue_blocks,
            max_dialogue_blocks=max_dialogue_blocks,
            require_event_mentions=bool(body_conf.get("require_event_mentions", True)),
        ),
        scenario_review=ScenarioReviewConfig(
            enabled=bool(review_conf.get("enabled", False)),
            require_human_review=bool(review_conf.get("require_human_review", False)),
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
