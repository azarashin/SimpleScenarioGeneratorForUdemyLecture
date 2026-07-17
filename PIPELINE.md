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

## Outputs

Generated under `output/<run-id>/`:
- `artifacts/*.json`
- `run-state.json`
- `trace.jsonl`
- `summary.json`
