from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config import ImageGenerationConfig, load_config
from pipeline.engine import ExecutionOptions, StepExecutionEngine
from pipeline.state import StepState
from pipeline.types import Step, StepContext, StepResult


class ConstantOutputStep(Step):
    def __init__(self, name: str, output: dict[str, object]) -> None:
        self.name = name
        self.output = output
        self.calls = 0

    def run(self, context: StepContext) -> StepResult:
        self.calls += 1
        return StepResult(
            output=self.output,
            prompt=f"prompt:{self.name}",
            model="test-model",
            temperature=0.1,
            input_tokens=10,
            output_tokens=20,
        )


class FlakyStep(Step):
    def __init__(self, name: str, fail_times: int, output: dict[str, object]) -> None:
        self.name = name
        self.fail_times = fail_times
        self.output = output
        self.calls = 0

    def run(self, context: StepContext) -> StepResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"planned-fail-{self.calls}")
        return StepResult(output=self.output)


def test_p0_skip_completed_step_and_load_artifact(make_context) -> None:
    """P0: completed artifact should be loaded and step execution skipped."""
    context, trace = make_context()
    step = ConstantOutputStep("step-a", {"a": 1})
    engine = StepExecutionEngine([step])

    artifact_path = Path(context.artifacts_dir) / "step-a.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps({"a": 999}), encoding="utf-8")
    context.state_store.upsert_step(
        StepState(
            name="step-a",
            status="completed",
            attempts=1,
            artifact_path=str(artifact_path),
        )
    )

    out = engine.run(context)

    assert step.calls == 0
    assert out["a"] == 999
    assert any(e.get("event") == "step_skipped" for e in trace.events)


def test_p0_from_step_preloads_dependencies(make_context) -> None:
    """P0: from-step restart should preload prerequisite artifacts."""
    context, trace = make_context(include_input=False)

    step1 = ConstantOutputStep("step-1", {"k1": "v1"})
    step2 = ConstantOutputStep("step-2", {"k2": "v2"})
    step3 = ConstantOutputStep("step-3", {"k3": "v3"})
    engine = StepExecutionEngine([step1, step2, step3])

    for name, payload in (("step-1", {"k1": "pre1"}), ("step-2", {"k2": "pre2"})):
        artifact = Path(context.artifacts_dir) / f"{name}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(payload), encoding="utf-8")
        context.state_store.upsert_step(
            StepState(name=name, status="completed", attempts=1, artifact_path=str(artifact))
        )

    out = engine.run(context, options=ExecutionOptions(from_step="step-3"))

    assert step1.calls == 0
    assert step2.calls == 0
    assert step3.calls == 1
    assert out["k1"] == "pre1"
    assert out["k2"] == "pre2"
    assert out["k3"] == "v3"
    assert sum(1 for e in trace.events if e.get("event") == "step_preloaded") == 2


def test_p0_from_step_fails_when_prerequisite_artifact_missing(make_context) -> None:
    """P0: restart from middle must fail if prerequisite artifact is missing."""
    context, _ = make_context(include_input=False)
    step1 = ConstantOutputStep("step-1", {"k1": "v1"})
    step2 = ConstantOutputStep("step-2", {"k2": "v2"})
    engine = StepExecutionEngine([step1, step2])

    with pytest.raises(RuntimeError, match="prerequisite artifact is missing"):
        engine.run(context, options=ExecutionOptions(from_step="step-2"))


def test_p0_retry_and_failure_state_tracking(make_context, base_config) -> None:
    """P0: failed step should be retried up to max_retries+1 and then complete when succeeding."""
    context, trace = make_context()
    flaky = FlakyStep("step-flaky", fail_times=1, output={"ok": True})
    engine = StepExecutionEngine([flaky])

    out = engine.run(context)

    assert flaky.calls == base_config.max_retries + 1
    assert out["ok"] is True
    state = context.state_store.get_step("step-flaky")
    assert state is not None
    assert state.status == "completed"
    assert state.attempts == base_config.max_retries + 1
    assert any(e.get("event") == "step_failed" for e in trace.events)
    assert any(e.get("event") == "step_succeeded" for e in trace.events)


def test_p1_force_true_reexecutes_completed_step(make_context) -> None:
    """P1: force option should bypass skip path and execute step again."""
    context, _ = make_context()
    step = ConstantOutputStep("step-force", {"v": 2})
    engine = StepExecutionEngine([step])

    artifact = Path(context.artifacts_dir) / "step-force.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"v": 1}), encoding="utf-8")
    context.state_store.upsert_step(
        StepState(name="step-force", status="completed", attempts=1, artifact_path=str(artifact))
    )

    out = engine.run(context, options=ExecutionOptions(force=True))

    assert step.calls == 1
    assert out["v"] == 2


def test_p1_unknown_from_step_should_error(make_context) -> None:
    """P1: unknown from-step should fail fast with explicit error."""
    context, _ = make_context()
    step = ConstantOutputStep("known-step", {"x": 1})
    engine = StepExecutionEngine([step])

    with pytest.raises(RuntimeError, match="Unknown from-step"):
        engine.run(context, options=ExecutionOptions(from_step="unknown-step"))


def test_p1_config_default_and_partial_override(tmp_path: Path) -> None:
    """P1: config loader should keep defaults and apply nested partial overrides."""
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "model_name": "overridden",
                "image_generation": {"provider": "stub"},
            }
        ),
        encoding="utf-8",
    )

    conf = load_config(str(cfg_path))

    assert conf.model_name == "overridden"
    assert conf.max_retries == 2
    assert conf.image_generation.provider == "stub"
    assert conf.image_generation.model == ImageGenerationConfig().model


def test_p1_cli_like_integration_creates_core_outputs(make_context) -> None:
    """P1: minimal integration should create state/trace/artifact files and support second-run skip."""
    context, _ = make_context()
    step = ConstantOutputStep("step-int", {"integrated": True})
    engine = StepExecutionEngine([step])

    out1 = engine.run(context)
    out2 = engine.run(context)

    assert out1["integrated"] is True
    assert out2["integrated"] is True
    assert Path(context.artifacts_dir, "step-int.json").exists()
    assert context.state_store.state_path.exists()
    trace_path = context.state_store.state_path.parent / context.config.trace_file_name
    assert trace_path.exists()
