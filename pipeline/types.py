from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StepResult:
    output: dict[str, Any]
    prompt: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    model: str | None = None
    temperature: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepContext:
    run_id: str
    config: "AppConfig"
    artifacts_dir: str
    state_store: "RunStateStore"
    trace_logger: "TraceLogger"
    shared_data: dict[str, Any]
    text_generation_provider: "TextGenerationProvider"


class Step:
    name: str
    schema_name: str | None = None
    input_keys: tuple[str, ...] = ()

    def run(self, context: StepContext) -> StepResult:
        raise NotImplementedError

    def run_with_prompt_revision(
        self, context: StepContext, failure_reason: str
    ) -> StepResult:
        """Retry with a corrected prompt. Override in model-backed steps."""
        return self.run(context)

    def run_fallback(self, context: StepContext, failure_reason: str) -> StepResult:
        """Produce the final fallback result. Override for deterministic fallback behavior."""
        return self.run(context)


from .config import AppConfig  # noqa: E402
from .state import RunStateStore  # noqa: E402
from .trace import TraceLogger  # noqa: E402
from .text_generation import TextGenerationProvider  # noqa: E402
