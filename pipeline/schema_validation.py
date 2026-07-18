from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


class StepSchemaValidationError(ValueError):
    """Raised when a step input or generated output violates its contract."""


class StepSchemaValidator:
    def __init__(self, schemas_dir: Path | None = None) -> None:
        self.schemas_dir = schemas_dir or Path(__file__).resolve().parent.parent / "schemas"
        self._registry = self._build_registry()

    def _build_registry(self) -> Registry:
        registry = Registry()
        for path in self.schemas_dir.glob("*.schema.json"):
            contents = json.loads(path.read_text(encoding="utf-8"))
            resource = Resource.from_contents(contents)
            registry = registry.with_resource(path.as_uri(), resource)
            schema_id = contents.get("$id")
            if schema_id:
                registry = registry.with_resource(schema_id, resource)
        return registry

    def validate(
        self,
        *,
        schema_name: str,
        section: str,
        instance: dict[str, Any],
    ) -> None:
        schema_path = self.schemas_dir / schema_name
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        section_schema = {
            "$id": schema_path.as_uri(),
            **schema["properties"][section],
        }
        validator = Draft202012Validator(
            section_schema,
            registry=self._registry,
        )
        errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
        if not errors:
            return

        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise StepSchemaValidationError(
            f"Schema validation failed ({schema_name}, {section}, {location}): "
            f"{error.message}"
        )
