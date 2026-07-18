from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pipeline.config import load_config
from pipeline.engine import ExecutionOptions, StepExecutionEngine
from pipeline.state import RunStateStore
from pipeline.steps import build_minimal_steps
from pipeline.trace import TraceLogger
from pipeline.text_generation import create_text_generation_provider
from pipeline.types import StepContext


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run minimal scenario pipeline")
    parser.add_argument("--config", type=str, default="examples/pipeline.config.json")
    parser.add_argument("--input", type=str, default="examples/input.json")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--from-step", type=str, default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    run_id = args.run_id or datetime.now().strftime("run-%Y%m%d-%H%M%S")
    run_root = Path(config.output_root) / run_id
    artifacts_dir = run_root / config.artifacts_dir_name
    state_file = run_root / config.state_file_name
    trace_file = run_root / config.trace_file_name

    input_data = json.loads(Path(args.input).read_text(encoding="utf-8"))

    context = StepContext(
        run_id=run_id,
        config=config,
        artifacts_dir=str(artifacts_dir),
        state_store=RunStateStore(state_file),
        trace_logger=TraceLogger(trace_file),
        shared_data={"input": input_data},
        text_generation_provider=create_text_generation_provider(
            config.text_generation.provider,
            timeout_seconds=config.text_generation.timeout_seconds,
            api_key_env=config.text_generation.api_key_env,
        ),
    )

    engine = StepExecutionEngine(build_minimal_steps())
    output = engine.run(
        context,
        options=ExecutionOptions(from_step=args.from_step, force=args.force),
    )

    summary_path = run_root / "summary.json"
    summary_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Run completed: {run_id}")
    print(f"Artifacts: {artifacts_dir}")
    print(f"State: {state_file}")
    print(f"Trace: {trace_file}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
