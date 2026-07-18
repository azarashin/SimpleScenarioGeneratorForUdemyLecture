from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from pipeline.consistency import ConsistencyCheckError, PipelineConsistencyChecker
from pipeline.engine import StepExecutionEngine
from pipeline.image_generation import MockImageGenerationProvider
from pipeline.steps import GenerateCharacterImagesStep, GenerateCharacterProfilesStep


def test_character_image_step_generates_base_and_expression_assets(make_context) -> None:
    context, _ = make_context()
    provider = MockImageGenerationProvider()
    context.image_generation_provider = provider

    output = StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateCharacterImagesStep()]
    ).run(context)

    assets = output["character_image_assets"]
    assert len(assets) == 1
    asset = assets[0]
    assert asset["character_id"] == "c001"
    assert asset["base_image_path"] == "assets/characters/c001/base.png"
    assert asset["expression_images"] == {
        "neutral": "assets/characters/c001/base.png",
        "happy": "assets/characters/c001/happy.png",
        "sad": "assets/characters/c001/sad.png",
    }
    run_root = Path(context.artifacts_dir).parent
    for relative_path in set(asset["expression_images"].values()):
        image_path = run_root / relative_path
        assert image_path.exists()
        assert image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert len(provider.requests) == 3
    assert provider.requests[0].model == "chat-gpt-image-2"


def test_character_image_step_records_artifact_and_trace(make_context) -> None:
    context, trace = make_context()

    StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateCharacterImagesStep()]
    ).run(context)

    artifact = Path(context.artifacts_dir) / "step-03-generate-character-images.json"
    assert artifact.exists()
    succeeded = [
        event
        for event in trace.events
        if event.get("event") == "step_succeeded"
        and event.get("step") == "step-03-generate-character-images"
    ]
    assert len(succeeded) == 1
    assert succeeded[0]["model"] == "chat-gpt-image-2"
    assert succeeded[0]["prompt_version"] == "v1"
    assert len(succeeded[0]["prompt_hash"]) == 64


def test_image_asset_consistency_rejects_missing_expression(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateCharacterImagesStep()]
    ).run(context)
    assets = deepcopy(output["character_image_assets"])
    del assets[0]["expression_images"]["sad"]

    with pytest.raises(ConsistencyCheckError, match="missing expressions.*sad"):
        PipelineConsistencyChecker().check(
            output,
            {"character_image_assets": assets},
            run_root=Path(context.artifacts_dir).parent,
        )


def test_image_asset_consistency_rejects_missing_or_corrupt_file(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateCharacterImagesStep()]
    ).run(context)
    run_root = Path(context.artifacts_dir).parent
    happy_path = (
        run_root
        / output["character_image_assets"][0]["expression_images"]["happy"]
    )
    happy_path.write_bytes(b"not an image")

    with pytest.raises(ConsistencyCheckError, match="not a supported image"):
        PipelineConsistencyChecker().check(
            output,
            {"character_image_assets": output["character_image_assets"]},
            run_root=run_root,
        )

    happy_path.unlink()
    with pytest.raises(ConsistencyCheckError, match="file does not exist"):
        PipelineConsistencyChecker().check(
            output,
            {"character_image_assets": output["character_image_assets"]},
            run_root=run_root,
        )


def test_image_asset_consistency_rejects_unsafe_path(make_context) -> None:
    context, _ = make_context()
    output = StepExecutionEngine(
        [GenerateCharacterProfilesStep(), GenerateCharacterImagesStep()]
    ).run(context)
    assets = deepcopy(output["character_image_assets"])
    assets[0]["base_image_path"] = "../outside.png"

    with pytest.raises(ConsistencyCheckError, match="safe relative path"):
        PipelineConsistencyChecker().check(
            output,
            {"character_image_assets": assets},
            run_root=Path(context.artifacts_dir).parent,
        )
