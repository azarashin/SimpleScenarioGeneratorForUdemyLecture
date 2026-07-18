from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StepState:
    name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int = 0
    retry_phase: str | None = None
    error_reason: str | None = None
    artifact_path: str | None = None


class RunStateStore:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write({"steps": {}})

    def _read(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_step(self, step_name: str) -> StepState | None:
        raw = self._read().get("steps", {}).get(step_name)
        if not raw:
            return None
        return StepState(
            name=step_name,
            status=str(raw.get("status", "pending")),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            attempts=int(raw.get("attempts", 0)),
            retry_phase=raw.get("retry_phase"),
            error_reason=raw.get("error_reason"),
            artifact_path=raw.get("artifact_path"),
        )

    def upsert_step(self, step: StepState) -> None:
        payload = self._read()
        payload.setdefault("steps", {})[step.name] = {
            "status": step.status,
            "started_at": step.started_at,
            "finished_at": step.finished_at,
            "attempts": step.attempts,
            "retry_phase": step.retry_phase,
            "error_reason": step.error_reason,
            "artifact_path": step.artifact_path,
        }
        self._write(payload)

    def all_steps(self) -> dict[str, StepState]:
        raw_steps = self._read().get("steps", {})
        result: dict[str, StepState] = {}
        for name, raw in raw_steps.items():
            result[name] = StepState(
                name=name,
                status=str(raw.get("status", "pending")),
                started_at=raw.get("started_at"),
                finished_at=raw.get("finished_at"),
                attempts=int(raw.get("attempts", 0)),
                retry_phase=raw.get("retry_phase"),
                error_reason=raw.get("error_reason"),
                artifact_path=raw.get("artifact_path"),
            )
        return result
