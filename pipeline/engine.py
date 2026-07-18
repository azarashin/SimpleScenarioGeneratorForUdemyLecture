from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .state import RunStateStore, StepState
from .schema_validation import StepSchemaValidator
from .types import Step, StepContext


@dataclass(slots=True)
class ExecutionOptions:
    from_step: str | None = None
    force: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StepExecutionEngine:
    def __init__(
        self,
        steps: list[Step],
        schema_validator: StepSchemaValidator | None = None,
    ) -> None:
        self.steps = steps
        self.schema_validator = schema_validator or StepSchemaValidator()

    def run(self, context: StepContext, options: ExecutionOptions | None = None) -> dict[str, object]:
        opts = options or ExecutionOptions()
        step_names = {step.name for step in self.steps}
        if opts.from_step is not None and opts.from_step not in step_names:
            raise RuntimeError(f"Unknown from-step: {opts.from_step}")

        seen_from = opts.from_step is None

        for step in self.steps:
            if opts.from_step and not seen_from:
                if step.name == opts.from_step:
                    seen_from = True
                else:
                    existing = context.state_store.get_step(step.name)
                    if (
                        existing
                        and existing.status == "completed"
                        and existing.artifact_path
                        and Path(existing.artifact_path).exists()
                    ):
                        output = json.loads(
                            Path(existing.artifact_path).read_text(encoding="utf-8")
                        )
                        context.shared_data.update(output)
                        context.trace_logger.log(
                            {
                                "run_id": context.run_id,
                                "step": step.name,
                                "event": "step_preloaded",
                                "reason": "from_step_dependency",
                            }
                        )
                        continue
                    raise RuntimeError(
                        "Cannot restart from step because prerequisite artifact is missing: "
                        f"{step.name}"
                    )

            existing = context.state_store.get_step(step.name)
            should_skip = bool(
                existing
                and existing.status == "completed"
                and existing.artifact_path
                and not opts.force
                and (opts.from_step is None)
            )
            if should_skip:
                artifact = Path(existing.artifact_path)
                if artifact.exists():
                    output = json.loads(artifact.read_text(encoding="utf-8"))
                    context.shared_data.update(output)
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": step.name,
                            "event": "step_skipped",
                            "reason": "already_completed",
                        }
                    )
                    continue

            success = self._run_single_step(step=step, context=context)
            if not success:
                raise RuntimeError(f"Step failed: {step.name}")

        return context.shared_data

    def _run_single_step(self, step: Step, context: StepContext) -> bool:
        max_attempts = max(1, context.config.max_retries + 1)
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            context.state_store.upsert_step(
                StepState(
                    name=step.name,
                    status="running",
                    started_at=_now_iso(),
                    attempts=attempts,
                )
            )

            started = time.perf_counter()
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": step.name,
                    "event": "step_started",
                    "attempt": attempts,
                }
            )

            try:
                if step.schema_name:
                    step_input = {
                        key: self._get_input_value(context, key) for key in step.input_keys
                    }
                    self.schema_validator.validate(
                        schema_name=step.schema_name,
                        section="input",
                        instance=step_input,
                    )
                result = step.run(context)
                if step.schema_name:
                    self.schema_validator.validate(
                        schema_name=step.schema_name,
                        section="output",
                        instance=result.output,
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)

                artifact_path = Path(context.artifacts_dir) / f"{step.name}.json"
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_text(
                    json.dumps(result.output, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                context.shared_data.update(result.output)
                context.state_store.upsert_step(
                    StepState(
                        name=step.name,
                        status="completed",
                        started_at=_now_iso(),
                        finished_at=_now_iso(),
                        attempts=attempts,
                        artifact_path=str(artifact_path),
                    )
                )
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": step.name,
                        "event": "step_succeeded",
                        "attempt": attempts,
                        "duration_ms": elapsed_ms,
                        "prompt": result.prompt,
                        "model": result.model or context.config.model_name,
                        "temperature": result.temperature
                        if result.temperature is not None
                        else context.config.temperature,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                    }
                )
                return True
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                reason = str(exc)
                context.state_store.upsert_step(
                    StepState(
                        name=step.name,
                        status="failed",
                        started_at=_now_iso(),
                        finished_at=_now_iso(),
                        attempts=attempts,
                        error_reason=reason,
                    )
                )
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": step.name,
                        "event": "step_failed",
                        "attempt": attempts,
                        "duration_ms": elapsed_ms,
                        "failure_reason": reason,
                    }
                )

        return False

    @staticmethod
    def _get_input_value(context: StepContext, key: str) -> object:
        if key in context.shared_data:
            return context.shared_data[key]
        pipeline_input = context.shared_data.get("input")
        if isinstance(pipeline_input, dict) and key in pipeline_input:
            return pipeline_input[key]
        raise KeyError(f"Missing schema input: {key}")
