from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class PromptImpactReporter:
    def compare(self, baseline_run: Path, candidate_run: Path) -> dict[str, Any]:
        baseline = self._summarize(baseline_run)
        candidate = self._summarize(candidate_run)
        steps: dict[str, Any] = {}
        for step_name in sorted(set(baseline) | set(candidate)):
            before = baseline.get(step_name, {})
            after = candidate.get(step_name, {})
            steps[step_name] = {
                "baseline_prompt_version": before.get("prompt_version"),
                "candidate_prompt_version": after.get("prompt_version"),
                "prompt_changed": before.get("prompt_hash") != after.get("prompt_hash"),
                "attempts_delta": after.get("attempts", 0) - before.get("attempts", 0),
                "failures_delta": after.get("failures", 0) - before.get("failures", 0),
                "duration_ms_delta": after.get("duration_ms", 0) - before.get("duration_ms", 0),
                "input_tokens_delta": after.get("input_tokens", 0) - before.get("input_tokens", 0),
                "output_tokens_delta": after.get("output_tokens", 0) - before.get("output_tokens", 0),
                "artifact_changed": before.get("artifact_hash") != after.get("artifact_hash"),
            }
        return {
            "baseline_run": baseline_run.name,
            "candidate_run": candidate_run.name,
            "steps": steps,
        }

    def _summarize(self, run_root: Path) -> dict[str, dict[str, Any]]:
        trace_path = run_root / "trace.jsonl"
        summary: dict[str, dict[str, Any]] = {}
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            step_name = event.get("step")
            if not step_name:
                continue
            step = summary.setdefault(step_name, {"attempts": 0, "failures": 0})
            if event.get("event") == "step_started":
                step["attempts"] += 1
                step["prompt_version"] = event.get("prompt_version")
                step["prompt_hash"] = event.get("prompt_hash")
            elif event.get("event") == "step_failed":
                step["failures"] += 1
            elif event.get("event") == "step_succeeded":
                step["prompt_version"] = event.get("prompt_version")
                step["prompt_hash"] = event.get("prompt_hash")
                for key in ("duration_ms", "input_tokens", "output_tokens"):
                    step[key] = event.get(key) or 0
                artifact = run_root / "artifacts" / f"{step_name}.json"
                if artifact.exists():
                    step["artifact_hash"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        return summary
