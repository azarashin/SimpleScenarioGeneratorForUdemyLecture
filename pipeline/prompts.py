from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PromptDefinition:
    step_name: str
    version: str
    text: str
    content_hash: str


class PromptCatalog:
    def __init__(self, catalog_path: Path | None = None) -> None:
        self.catalog_path = catalog_path or (
            Path(__file__).resolve().parent.parent / "prompts" / "catalog.json"
        )
        self._catalog: dict[str, dict[str, str]] = json.loads(
            self.catalog_path.read_text(encoding="utf-8")
        )

    def resolve(self, step_name: str, requested_version: str | None = None) -> PromptDefinition:
        versions = self._catalog.get(step_name)
        if not versions:
            raise KeyError(f"Prompt is not registered for step: {step_name}")
        version = requested_version or list(versions)[-1]
        if version not in versions:
            raise KeyError(f"Unknown prompt version for {step_name}: {version}")
        text = versions[version]
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return PromptDefinition(step_name, version, text, content_hash)


def resolve_step_prompt(context: Any, step_name: str) -> PromptDefinition:
    requested = context.config.prompt_versions.get(step_name)
    return PromptCatalog().resolve(step_name, requested)
