import json

import pytest

from pipeline.artifact_loader import ArtifactLoadError, PipelineArtifactLoader


def test_loader_preserves_existing_values_and_loads_missing_atomically(tmp_path):
    existing = [{"source": "memory"}]
    (tmp_path / "step-02-generate-outline.json").write_text(
        json.dumps({"scenario_outline": {"title": "loaded"}}), encoding="utf-8"
    )
    shared = {"character_profiles": existing}

    loaded = PipelineArtifactLoader(tmp_path).load_missing(
        shared,
        required_keys=("character_profiles", "scenario_outline"),
    )

    assert loaded == ("scenario_outline",)
    assert shared["character_profiles"] is existing
    assert shared["scenario_outline"]["title"] == "loaded"


def test_loader_does_not_partially_update_when_required_artifact_is_missing(tmp_path):
    (tmp_path / "step-02-generate-outline.json").write_text(
        json.dumps({"scenario_outline": {"title": "loaded"}}), encoding="utf-8"
    )
    shared = {}

    with pytest.raises(ArtifactLoadError, match="does not exist"):
        PipelineArtifactLoader(tmp_path).load_missing(
            shared,
            required_keys=("scenario_outline", "scenario_sections"),
        )

    assert shared == {}
