from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


class HtmlValidationError(ValueError):
    """Raised when generated HTML contains a broken or unsafe local reference."""


@dataclass(frozen=True, slots=True)
class HtmlValidationReport:
    page_count: int
    link_count: int
    image_count: int


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {name.casefold(): value for name, value in attrs if value is not None}
        if tag.casefold() == "a" and "href" in values:
            self.links.append(values["href"])
        if tag.casefold() == "img" and "src" in values:
            self.images.append(values["src"])


class HtmlOutputValidator:
    """Validate every generated page and its local links and image paths."""

    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root.resolve()

    def validate(self, page_paths: Iterable[str]) -> HtmlValidationReport:
        paths = tuple(page_paths)
        if len(paths) != len(set(paths)):
            raise HtmlValidationError("HTML page manifest contains duplicate paths")

        link_count = 0
        image_count = 0
        for relative_path in paths:
            page = self._resolve_manifest_path(relative_path)
            if not page.is_file():
                raise HtmlValidationError(f"Generated HTML page does not exist: {relative_path}")
            try:
                html = page.read_text(encoding="utf-8")
            except UnicodeError as exc:
                raise HtmlValidationError(
                    f"Generated HTML page is not valid UTF-8: {relative_path}"
                ) from exc
            parser = _ReferenceParser()
            parser.feed(html)

            for href in parser.links:
                if self._is_external_or_fragment(href):
                    continue
                self._require_file(page, href, kind="link")
                link_count += 1
            for src in parser.images:
                parsed = urlsplit(src)
                if parsed.scheme or parsed.netloc:
                    raise HtmlValidationError(
                        f"Image must use a local path in {relative_path}: {src!r}"
                    )
                self._require_file(page, src, kind="image")
                image_count += 1

        return HtmlValidationReport(
            page_count=len(paths),
            link_count=link_count,
            image_count=image_count,
        )

    def _resolve_manifest_path(self, relative_path: str) -> Path:
        return self._resolve_beneath_root(self.run_root, relative_path, "page")

    def _require_file(self, source_page: Path, reference: str, *, kind: str) -> None:
        parsed = urlsplit(reference)
        decoded_path = unquote(parsed.path)
        if not decoded_path:
            return
        target = self._resolve_beneath_root(source_page.parent, decoded_path, kind)
        if not target.is_file():
            source = source_page.relative_to(self.run_root).as_posix()
            raise HtmlValidationError(
                f"Broken {kind} in {source}: {reference!r} resolves to missing file"
            )

    def _resolve_beneath_root(self, base: Path, value: str, kind: str) -> Path:
        if not value or "\\" in value or Path(value).is_absolute():
            raise HtmlValidationError(f"Unsafe {kind} path: {value!r}")
        target = (base / value).resolve()
        try:
            target.relative_to(self.run_root)
        except ValueError as exc:
            raise HtmlValidationError(
                f"{kind.capitalize()} path escapes the run directory: {value!r}"
            ) from exc
        return target

    @staticmethod
    def _is_external_or_fragment(href: str) -> bool:
        parsed = urlsplit(href)
        return bool(parsed.scheme or parsed.netloc or (not parsed.path and parsed.fragment))
