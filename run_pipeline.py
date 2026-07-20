from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from pipeline.config import load_config
from pipeline.engine import ExecutionOptions, StepExecutionEngine
from pipeline.image_generation import create_image_generation_provider
from pipeline.state import RunStateStore
from pipeline.steps import build_minimal_steps
from pipeline.trace import TraceLogger
from pipeline.text_generation import create_text_generation_provider
from pipeline.types import StepContext


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the scenario generation pipeline")
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

    input_text = Path(args.input).read_text(encoding="utf-8")
    try:
        parsed_input = json.loads(input_text)
    except json.JSONDecodeError:
        parsed_input = None
    is_structured_input = bool(
        isinstance(parsed_input, dict)
        and "scenario_idea" in parsed_input
        and "character_overviews" in parsed_input
    )
    if not is_structured_input and not config.planning_input_generation.enabled:
        raise ValueError(
            "Free-form input requires planning_input_generation.enabled=true."
        )
    shared_data = (
        {"input": parsed_input}
        if is_structured_input
        else {"rough_idea": input_text.strip()}
    )

    context = StepContext(
        run_id=run_id,
        config=config,
        artifacts_dir=str(artifacts_dir),
        state_store=RunStateStore(state_file),
        trace_logger=TraceLogger(trace_file),
        shared_data=shared_data,
        text_generation_provider=create_text_generation_provider(
            config.text_generation.provider,
            timeout_seconds=config.text_generation.timeout_seconds,
            api_key_env=config.text_generation.api_key_env,
        ),
        image_generation_provider=create_image_generation_provider(
            config.image_generation.provider,
            quality=config.image_generation.quality,
            output_format=config.image_generation.output_format,
            timeout_seconds=config.image_generation.timeout_seconds,
            api_key_env=config.image_generation.api_key_env,
        ),
    )

    engine = StepExecutionEngine(
        build_minimal_steps(include_planning_input_generation=not is_structured_input)
    )
    output = engine.run(
        context,
        options=ExecutionOptions(from_step=args.from_step, force=args.force),
    )

    summary_path = run_root / "summary.json"
    summary_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    if context.paused_after_step:
        print(f"Run paused for review after: {context.paused_after_step}")
        print(
            "Review artifact: "
            f"{artifacts_dir / f'{context.paused_after_step}.json'}"
        )
    else:
        print(f"Run completed: {run_id}")
    print(f"Artifacts: {artifacts_dir}")
    print(f"State: {state_file}")
    print(f"Trace: {trace_file}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
