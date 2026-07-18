from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config import AppConfig, ImageGenerationConfig
from pipeline.state import RunStateStore
from pipeline.trace import TraceLogger
from pipeline.types import StepContext


class MemoryTraceLogger:
    def __init__(self, trace_path: Path) -> None:
        self.trace_path = trace_path
        self.events: list[dict[str, object]] = []
        self._delegate = TraceLogger(trace_path)

    def log(self, event: dict[str, object]) -> None:
        self.events.append(event)
        self._delegate.log(event)


@pytest.fixture
def base_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        model_name="test-model",
        temperature=0.4,
        output_root=str(tmp_path),
        artifacts_dir_name="artifacts",
        state_file_name="run-state.json",
        trace_file_name="trace.jsonl",
        image_generation=ImageGenerationConfig(),
    )


@pytest.fixture
def input_payload() -> dict[str, object]:
    return {
        "scenario_idea": {
            "title": "t",
            "genre": "g",
            "theme": "th",
            "premise": "p",
            "target_length": {"chapter_count": 1, "sections_per_chapter": 1},
        },
        "character_overviews": [
            {
                "character_id": "c001",
                "name": "n",
                "role": "r",
                "summary": "s",
            }
        ],
    }


@pytest.fixture
def make_context(tmp_path: Path, base_config: AppConfig, input_payload: dict[str, object]):
    def _make(run_id: str = "run-test", include_input: bool = True) -> tuple[StepContext, MemoryTraceLogger]:
        run_root = tmp_path / run_id
        artifacts_dir = run_root / base_config.artifacts_dir_name
        state_file = run_root / base_config.state_file_name
        trace_file = run_root / base_config.trace_file_name

        shared_data: dict[str, object] = {}
        if include_input:
            shared_data["input"] = input_payload

        trace = MemoryTraceLogger(trace_file)
        context = StepContext(
            run_id=run_id,
            config=base_config,
            artifacts_dir=str(artifacts_dir),
            state_store=RunStateStore(state_file),
            trace_logger=trace,
            shared_data=shared_data,
        )
        return context, trace

    return _make


def write_artifact(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
