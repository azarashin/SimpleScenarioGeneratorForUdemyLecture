# Minimal Pipeline Runner

## Overview

This project includes a minimal executable pipeline built on top of the step engine.

Implemented minimal steps:
- step-01-generate-character-profiles
- step-02-generate-outline
- step-03-generate-sections

## Run

```powershell
python run_pipeline.py --config examples/pipeline.config.json --input examples/input.json
```

## Resume

```powershell
python run_pipeline.py --run-id run-20260718-101010
```

## Restart from a Step

```powershell
python run_pipeline.py --run-id run-20260718-101010 --from-step step-03-generate-sections
```

## Force Re-run

```powershell
python run_pipeline.py --run-id run-20260718-101010 --force
```

## Retry Strategy

Retries run in separate phases: a short retry, a prompt-revision retry that receives
the previous failure reason, and one final fallback attempt.

```json
{
  "retry_strategy": {
    "short_retries": 1,
    "prompt_revision_retries": 1,
    "fallback_enabled": true
  }
}
```

Model-backed steps can override `run_with_prompt_revision` and `run_fallback` to
provide phase-specific behavior. Trace events and run state include `retry_phase`.

## Consistency Checks

Before an artifact is saved, the runner automatically rejects contradictions in
character IDs, names, and roles; ambiguous normalized character names; unknown
participants or speakers; duplicate block IDs; and chapter/section timeline drift.
Failures enter the configured retry strategy and successful checks emit a
`consistency_checked` trace event.

## Outputs

Generated under `output/<run-id>/`:
- `artifacts/*.json`
- `run-state.json`
- `trace.jsonl`
- `summary.json`
