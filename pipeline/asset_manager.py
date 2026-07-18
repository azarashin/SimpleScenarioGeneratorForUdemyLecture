from __future__ import annotations

from dataclasses import dataclass
import posixpath
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


class AssetManifestError(ValueError):
    """Raised when a character image manifest is ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class ResolvedCharacterImage:
    character_id: str
    speaker_name: str
    requested_expression: str
    resolved_expression: str | None
    image_path: str | None
    alt: str
    is_fallback: bool


class CharacterAssetResolver:
    """Resolve character images by character ID and expression label."""

    def __init__(
        self,
        assets: Iterable[dict[str, Any]],
        *,
        character_profiles: Iterable[dict[str, Any]] = (),
        run_root: Path | None = None,
        verify_files: bool = False,
    ) -> None:
        if verify_files and run_root is None:
            raise AssetManifestError("run_root is required when verify_files is enabled")
        self._run_root = run_root.resolve() if run_root is not None else None
        self._verify_files = verify_files
        self._profiles = self._index_unique(character_profiles, "character profiles")
        self._assets = self._index_unique(assets, "character image assets")
        for asset in self._assets.values():
            self._validate_path(str(asset["base_image_path"]))
            for path in asset.get("expression_images", {}).values():
                self._validate_path(str(path))

    @classmethod
    def from_pipeline_data(
        cls,
        data: dict[str, Any],
        *,
        run_root: Path | None = None,
        verify_files: bool = False,
    ) -> CharacterAssetResolver:
        """Create a resolver directly from pipeline shared/output data."""
        return cls(
            data["character_image_assets"],
            character_profiles=data.get("character_profiles", ()),
            run_root=run_root,
            verify_files=verify_files,
        )

    def resolve(
        self,
        character_id: str,
        expression: str,
        *,
        relative_to: str = ".",
    ) -> ResolvedCharacterImage:
        """Resolve an HTML-ready URL, falling back to base and then no image."""
        profile = self._profiles.get(character_id, {})
        speaker_name = str(profile.get("name", character_id))
        asset = self._assets.get(character_id)
        candidates: list[tuple[str, str]] = []
        if asset is not None:
            expression_path = asset.get("expression_images", {}).get(expression)
            if expression_path:
                candidates.append((expression, str(expression_path)))
            base_path = asset.get("base_image_path")
            if base_path and (not expression_path or str(base_path) != str(expression_path)):
                candidates.append(("base", str(base_path)))

        for resolved_expression, path in candidates:
            if not self._exists(path):
                continue
            url = self._relative_url(path, relative_to=relative_to)
            return ResolvedCharacterImage(
                character_id=character_id,
                speaker_name=speaker_name,
                requested_expression=expression,
                resolved_expression=resolved_expression,
                image_path=url,
                alt=f"{speaker_name} - {resolved_expression}",
                is_fallback=resolved_expression != expression,
            )

        return ResolvedCharacterImage(
            character_id=character_id,
            speaker_name=speaker_name,
            requested_expression=expression,
            resolved_expression=None,
            image_path=None,
            alt=f"{speaker_name} - image unavailable",
            is_fallback=True,
        )

    @staticmethod
    def _index_unique(
        items: Iterable[dict[str, Any]], source: str
    ) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for item in items:
            character_id = str(item["character_id"])
            if character_id in indexed:
                raise AssetManifestError(
                    f"duplicate character_id {character_id!r} in {source}"
                )
            indexed[character_id] = item
        return indexed

    @staticmethod
    def _validate_path(value: str) -> None:
        path = PurePosixPath(value)
        if not value or "\\" in value or path.is_absolute() or ".." in path.parts:
            raise AssetManifestError(f"image path must be a safe relative path: {value!r}")

    def _exists(self, relative_path: str) -> bool:
        if not self._verify_files:
            return True
        assert self._run_root is not None
        resolved = (self._run_root / PurePosixPath(relative_path)).resolve()
        try:
            resolved.relative_to(self._run_root)
        except ValueError as exc:
            raise AssetManifestError(
                f"image path escapes the run directory: {relative_path!r}"
            ) from exc
        return resolved.is_file()

    @classmethod
    def _relative_url(cls, asset_path: str, *, relative_to: str) -> str:
        cls._validate_path(asset_path)
        if relative_to not in {"", "."}:
            cls._validate_path(relative_to)
        relative = posixpath.relpath(asset_path, start=relative_to or ".")
        return quote(relative, safe="/._-~")
