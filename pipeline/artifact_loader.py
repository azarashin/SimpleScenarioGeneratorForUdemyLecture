from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


class ArtifactLoadError(RuntimeError):
    """Raised when a prerequisite pipeline artifact cannot be restored."""


class PipelineArtifactLoader:
    ARTIFACTS = {
        "character_profiles": "step-01-generate-character-profiles.json",
        "scenario_outline": "step-02-generate-outline.json",
        "character_image_assets": "step-03-generate-character-images.json",
        "scenario_sections": "step-04-generate-sections.json",
        "dialogue_expression_tags": "step-05-generate-dialogue-tags.json",
    }

    def __init__(self, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    def load_missing(
        self,
        shared_data: dict[str, Any],
        *,
        required_keys: Iterable[str],
        optional_keys: Iterable[str] = (),
    ) -> tuple[str, ...]:
        """Load missing outputs atomically without replacing in-memory values."""
        pending: dict[str, Any] = {}
        loaded: list[str] = []
        for key, required in [
            *((key, True) for key in required_keys),
            *((key, False) for key in optional_keys),
        ]:
            if key in shared_data or key in pending:
                continue
            filename = self.ARTIFACTS.get(key)
            if filename is None:
                raise ArtifactLoadError(f"No artifact is registered for output: {key}")
            path = self.artifacts_dir / filename
            if not path.is_file():
                if required:
                    raise ArtifactLoadError(
                        f"Required artifact for {key} does not exist: {path}"
                    )
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ArtifactLoadError(f"Artifact is not valid UTF-8 JSON: {path}") from exc
            if not isinstance(payload, dict) or key not in payload:
                raise ArtifactLoadError(
                    f"Artifact does not contain expected output {key!r}: {path}"
                )
            pending[key] = payload[key]
            loaded.append(key)

        shared_data.update(pending)
        return tuple(loaded)
