from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .consistency import PipelineConsistencyChecker
from .state import RunStateStore, StepState
from .schema_validation import StepSchemaValidator
from .types import Step, StepContext, StepResult


@dataclass(slots=True)
class ExecutionOptions:
    from_step: str | None = None
    force: bool = False


@dataclass(frozen=True, slots=True)
class AttemptPlan:
    phase: str
    phase_attempt: int


class DeterminismPolicyError(ValueError):
    """Raised when a step attempts to bypass the configured temperature policy."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StepExecutionEngine:
    def __init__(
        self,
        steps: list[Step],
        schema_validator: StepSchemaValidator | None = None,
        consistency_checker: PipelineConsistencyChecker | None = None,
    ) -> None:
        self.steps = steps
        self.schema_validator = schema_validator or StepSchemaValidator()
        self.consistency_checker = consistency_checker or PipelineConsistencyChecker()

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
        plans = self._build_attempt_plans(context)
        last_failure_reason = ""

        for attempts, plan in enumerate(plans, start=1):
            context.state_store.upsert_step(
                StepState(
                    name=step.name,
                    status="running",
                    started_at=_now_iso(),
                    attempts=attempts,
                    retry_phase=plan.phase,
                )
            )

            started = time.perf_counter()
            context.trace_logger.log(
                {
                    "run_id": context.run_id,
                    "step": step.name,
                    "event": "step_started",
                    "attempt": attempts,
                    "retry_phase": plan.phase,
                    "phase_attempt": plan.phase_attempt,
                    "prompt_version": context.config.prompt_versions.get(step.name),
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
                result = self._execute_attempt(
                    step=step,
                    context=context,
                    plan=plan,
                    failure_reason=last_failure_reason,
                )
                effective_temperature = context.config.temperature_for(step.name)
                if (
                    result.temperature is not None
                    and result.temperature != effective_temperature
                ):
                    raise DeterminismPolicyError(
                        f"Temperature policy violation for {step.name}: "
                        f"expected {effective_temperature}, got {result.temperature}"
                    )
                if step.schema_name:
                    self.schema_validator.validate(
                        schema_name=step.schema_name,
                        section="output",
                        instance=result.output,
                    )
                consistency_data = {
                    **context.shared_data,
                    "_scenario_body_generation_config": {
                        "min_characters": (
                            context.config.scenario_body_generation.min_characters
                        ),
                        "max_characters": (
                            context.config.scenario_body_generation.max_characters
                        ),
                        "require_event_mentions": (
                            context.config.scenario_body_generation.require_event_mentions
                        ),
                    },
                }
                self.consistency_checker.check(consistency_data, result.output)
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": step.name,
                        "event": "consistency_checked",
                        "attempt": attempts,
                        "retry_phase": plan.phase,
                    }
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)

                artifact_path = Path(context.artifacts_dir) / f"{step.name}.json"
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                temp_artifact_path = artifact_path.with_suffix(".tmp")
                temp_artifact_path.write_text(
                    json.dumps(result.output, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temp_artifact_path.replace(artifact_path)

                context.shared_data.update(result.output)
                context.state_store.upsert_step(
                    StepState(
                        name=step.name,
                        status="completed",
                        started_at=_now_iso(),
                        finished_at=_now_iso(),
                        attempts=attempts,
                        retry_phase=plan.phase,
                        artifact_path=str(artifact_path),
                    )
                )
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": step.name,
                        "event": "step_succeeded",
                        "attempt": attempts,
                        "retry_phase": plan.phase,
                        "phase_attempt": plan.phase_attempt,
                        "duration_ms": elapsed_ms,
                        "prompt": result.prompt,
                        "prompt_version": result.prompt_version,
                        "prompt_hash": result.prompt_hash,
                        "model": result.model or context.config.model_name,
                        "temperature": effective_temperature,
                        "temperature_mode": (
                            "diversity"
                            if step.name in context.config.temperature_policy.diversity_steps
                            else "deterministic"
                        ),
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                    }
                )
                return True
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                reason = str(exc)
                last_failure_reason = reason
                context.state_store.upsert_step(
                    StepState(
                        name=step.name,
                        status="failed",
                        started_at=_now_iso(),
                        finished_at=_now_iso(),
                        attempts=attempts,
                        retry_phase=plan.phase,
                        error_reason=reason,
                    )
                )
                context.trace_logger.log(
                    {
                        "run_id": context.run_id,
                        "step": step.name,
                        "event": "step_failed",
                        "attempt": attempts,
                        "retry_phase": plan.phase,
                        "phase_attempt": plan.phase_attempt,
                        "duration_ms": elapsed_ms,
                        "failure_reason": reason,
                    }
                )

                next_index = attempts
                if next_index < len(plans):
                    next_plan = plans[next_index]
                    context.trace_logger.log(
                        {
                            "run_id": context.run_id,
                            "step": step.name,
                            "event": "step_retry_scheduled",
                            "attempt": attempts + 1,
                            "retry_phase": next_plan.phase,
                            "phase_attempt": next_plan.phase_attempt,
                            "previous_failure_reason": reason,
                        }
                    )

        return False

    @staticmethod
    def _build_attempt_plans(context: StepContext) -> list[AttemptPlan]:
        strategy = context.config.retry_strategy
        plans = [AttemptPlan("initial", 1)]
        plans.extend(
            AttemptPlan("short_retry", index)
            for index in range(1, strategy.short_retries + 1)
        )
        plans.extend(
            AttemptPlan("prompt_revision", index)
            for index in range(1, strategy.prompt_revision_retries + 1)
        )
        if strategy.fallback_enabled:
            plans.append(AttemptPlan("fallback", 1))
        return plans

    @staticmethod
    def _execute_attempt(
        *,
        step: Step,
        context: StepContext,
        plan: AttemptPlan,
        failure_reason: str,
    ) -> StepResult:
        if plan.phase == "prompt_revision":
            return step.run_with_prompt_revision(context, failure_reason)
        if plan.phase == "fallback":
            return step.run_fallback(context, failure_reason)
        return step.run(context)

    @staticmethod
    def _get_input_value(context: StepContext, key: str) -> object:
        if key in context.shared_data:
            return context.shared_data[key]
        pipeline_input = context.shared_data.get("input")
        if isinstance(pipeline_input, dict) and key in pipeline_input:
            return pipeline_input[key]
        raise KeyError(f"Missing schema input: {key}")
