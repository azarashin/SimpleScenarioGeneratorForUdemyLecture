from __future__ import annotations

from pathlib import Path, PurePosixPath


class HtmlOutputPathError(ValueError):
    """Raised when an HTML output path escapes or violates the run boundary."""


class HtmlOutputWriter:
    """Write UTF-8 HTML atomically beneath one pipeline run directory."""

    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root.resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)

    def write(self, relative_path: str, html: str) -> str:
        normalized = self._validate(relative_path)
        destination = (self.run_root / Path(*normalized.parts)).resolve()
        try:
            destination.relative_to(self.run_root)
        except ValueError as exc:
            raise HtmlOutputPathError(
                f"HTML output path escapes the run directory: {relative_path!r}"
            ) from exc

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.tmp")
        try:
            temporary.write_text(html, encoding="utf-8")
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return normalized.as_posix()

    @staticmethod
    def _validate(relative_path: str) -> PurePosixPath:
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or ".." in path.parts
            or bool(path.parts and ":" in path.parts[0])
            or path.suffix.casefold() != ".html"
        ):
            raise HtmlOutputPathError(
                f"HTML output must be a safe relative .html path: {relative_path!r}"
            )
        return path
