# Minimal Pipeline Runner

## Overview

This project includes a minimal executable pipeline built on top of the step engine.

Implemented minimal steps:
- step-01-generate-character-profiles
- step-02-generate-outline
- step-03-generate-sections

The generation contract for scenario sections is defined in `SCENARIO_BODY_SPEC.md`.
Text generation is accessed through `TextGenerationProvider`; local runs and tests use
the deterministic `MockTextGenerationProvider` implementation.

Text generation connection settings live under `text_generation`. Store only the API
key environment-variable name in JSON; never put the secret itself in configuration.

```json
{
  "text_generation": {
    "provider": "mock",
    "model": "gpt-4.1-mini",
    "timeout_seconds": 60,
    "api_key_env": "TEXT_GENERATION_API_KEY"
  }
}
```

For a future network-backed provider, set the secret in the process environment:

```powershell
$env:TEXT_GENERATION_API_KEY = "your-api-key"
```

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

## Temperature Policy

Generation uses a low temperature by default. A higher diversity temperature is
allowed only for explicitly listed creative steps; all other steps are fixed to the
low value. A step result that reports another temperature is rejected.

```json
{
  "temperature_policy": {
    "low_temperature": 0.2,
    "diversity_temperature": 0.7,
    "diversity_steps": [
      "step-02-generate-outline",
      "step-03-generate-sections"
    ]
  }
}
```

Trace events record both `temperature` and `temperature_mode`.

## Prompt Versions and Impact Comparison

Prompts are stored in `prompts/catalog.json`. Pin versions per step so reruns remain
reproducible; if omitted, the latest catalog version is selected. Every successful
trace records `prompt_version` and a SHA-256 `prompt_hash`.

```json
{
  "prompt_versions": {
    "step-01-generate-character-profiles": "v1",
    "step-02-generate-outline": "v2",
    "step-03-generate-sections": "v1"
  }
}
```

Compare two completed runs:

```powershell
python compare_prompt_runs.py output/run-baseline output/run-candidate
```

The report shows prompt changes and deltas for attempts, failures, duration, input
and output tokens, plus whether each generated artifact changed.

## Outputs

Generated under `output/<run-id>/`:
- `artifacts/*.json`
- `run-state.json`
- `trace.jsonl`
- `summary.json`
