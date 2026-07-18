from __future__ import annotations

from pathlib import Path

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
