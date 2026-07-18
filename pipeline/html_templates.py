from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Iterable

from .asset_manager import CharacterAssetResolver, ResolvedCharacterImage


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass(frozen=True, slots=True)
class SectionNavigation:
    """Relative links available from one generated section page."""

    previous_href: str | None
    next_href: str | None
    chapter_href: str = "index.html"
    index_href: str = "../index.html"


@dataclass(frozen=True, slots=True)
class ChapterNavigation:
    """Relative links available from one generated chapter page."""

    previous_href: str | None
    next_href: str | None
    index_href: str = "../index.html"


def build_chapter_navigation(
    outline: dict[str, Any], *, chapter_no: int
) -> ChapterNavigation:
    chapter_numbers = [int(chapter["chapter_no"]) for chapter in outline["chapters"]]
    try:
        position = chapter_numbers.index(chapter_no)
    except ValueError as exc:
        raise ValueError(f"Chapter is not present in outline: chapter {chapter_no}") from exc

    def href(target_chapter: int) -> str:
        return f"../chapter-{target_chapter}/index.html"

    return ChapterNavigation(
        previous_href=href(chapter_numbers[position - 1]) if position > 0 else None,
        next_href=(
            href(chapter_numbers[position + 1])
            if position + 1 < len(chapter_numbers)
            else None
        ),
    )


def build_section_navigation(
    outline: dict[str, Any], *, chapter_no: int, section_no: int
) -> SectionNavigation:
    """Build previous/next links in outline order, including chapter boundaries."""
    locations = [
        (int(chapter["chapter_no"]), int(section["section_no"]))
        for chapter in outline["chapters"]
        for section in chapter["sections"]
    ]
    target = (chapter_no, section_no)
    try:
        position = locations.index(target)
    except ValueError as exc:
        raise ValueError(
            f"Section is not present in outline: chapter {chapter_no}, section {section_no}"
        ) from exc

    def href(location: tuple[int, int]) -> str:
        target_chapter, target_section = location
        if target_chapter == chapter_no:
            return f"section-{target_section}.html"
        return f"../chapter-{target_chapter}/section-{target_section}.html"

    return SectionNavigation(
        previous_href=href(locations[position - 1]) if position > 0 else None,
        next_href=href(locations[position + 1]) if position + 1 < len(locations) else None,
    )


def _template(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def render_index_page(*, outline: dict[str, Any]) -> str:
    """Render a table of contents linking every chapter and section."""
    chapters = []
    for chapter in outline["chapters"]:
        chapter_no = int(chapter["chapter_no"])
        sections = []
        for section in chapter["sections"]:
            section_no = int(section["section_no"])
            sections.append(
                '<li><a href="chapter-{0}/section-{1}.html">第{1}節 {2}</a></li>'.format(
                    chapter_no, section_no, escape(str(section["section_title"]))
                )
            )
        chapters.append(
            '<li class="chapter"><h2><a href="chapter-{0}/index.html">第{0}章 {1}</a></h2>'
            '<ol class="sections">{2}</ol></li>'.format(
                chapter_no,
                escape(str(chapter["chapter_title"])),
                "".join(sections),
            )
        )
    return _template("index.html").substitute(
        work_title=escape(str(outline["title"])),
        logline=escape(str(outline.get("logline", ""))),
        chapter_links="".join(chapters),
    )


def render_chapter_page(
    *,
    work_title: str,
    chapter: dict[str, Any],
    outline: dict[str, Any] | None = None,
    previous_chapter_href: str | None = None,
    next_chapter_href: str | None = None,
    index_href: str = "../index.html",
) -> str:
    """Render one chapter landing page from a scenario-outline chapter."""
    chapter_no = int(chapter["chapter_no"])
    if outline is not None:
        navigation = build_chapter_navigation(outline, chapter_no=chapter_no)
        previous_chapter_href = navigation.previous_href
        next_chapter_href = navigation.next_href
        index_href = navigation.index_href
    links = []
    for section in chapter["sections"]:
        section_no = int(section["section_no"])
        links.append(
            '<li><a href="section-{0}.html"><span class="section-no">節 {0}</span>'
            '<span>{1}</span></a></li>'.format(
                section_no, escape(str(section["section_title"]))
            )
        )
    return _template("chapter.html").substitute(
        work_title=escape(work_title),
        chapter_label=f"第{chapter_no}章",
        chapter_title=escape(str(chapter["chapter_title"])),
        chapter_goal=escape(str(chapter["chapter_goal"])),
        section_links="".join(links),
        previous_chapter_link=_nav_link(
            previous_chapter_href, "前の章", "previous"
        ),
        next_chapter_link=_nav_link(next_chapter_href, "次の章", "next"),
        index_href=escape(index_href, quote=True),
    )


def render_section_page(
    *,
    work_title: str,
    chapter: dict[str, Any],
    section: dict[str, Any],
    dialogue_tags: Iterable[dict[str, Any]] = (),
    characters: dict[str, dict[str, Any]] | None = None,
    asset_resolver: CharacterAssetResolver | None = None,
    outline: dict[str, Any] | None = None,
    previous_href: str | None = None,
    next_href: str | None = None,
    chapter_href: str = "index.html",
    index_href: str = "../index.html",
) -> str:
    """Render a section page; all user-authored text and attributes are escaped."""
    if outline is not None:
        navigation = build_section_navigation(
            outline,
            chapter_no=int(chapter["chapter_no"]),
            section_no=int(section["section_no"]),
        )
        previous_href = navigation.previous_href
        next_href = navigation.next_href
        chapter_href = navigation.chapter_href
        index_href = navigation.index_href

    chapter_no = int(section["chapter_no"])
    section_no = int(section["section_no"])
    tag_by_block = {
        str(tag["block_id"]): tag
        for tag in dialogue_tags
        if int(tag.get("chapter_no", chapter_no)) == chapter_no
        and int(tag.get("section_no", section_no)) == section_no
    }
    character_map = characters or {}
    blocks = []
    for block in section["narrative_blocks"]:
        text = escape(str(block["text"]))
        if block["type"] == "narration":
            blocks.append(f'<p class="narration">{text}</p>')
            continue

        tag = tag_by_block.get(str(block["block_id"]), {})
        expression = str(tag.get("expression", "base"))
        resolved_image = (
            asset_resolver.resolve(
                str(block["speaker_id"]),
                expression,
                relative_to=f"chapter-{chapter_no}",
            )
            if asset_resolver is not None
            else None
        )
        blocks.append(
            render_dialogue_row(
                block=block,
                expression=expression,
                character=character_map.get(str(block["speaker_id"]), {}),
                resolved_image=resolved_image,
            )
        )

    return _template("section.html").substitute(
        work_title=escape(work_title),
        chapter_label=f"第{int(chapter['chapter_no'])}章 {escape(str(chapter['chapter_title']))}",
        section_label=f"第{int(section['section_no'])}節",
        section_title=escape(str(section["section_title"])),
        narrative_blocks="".join(blocks),
        previous_link=_nav_link(previous_href, "前の節", "previous"),
        next_link=_nav_link(next_href, "次の節", "next"),
        chapter_href=escape(chapter_href, quote=True),
        index_href=escape(index_href, quote=True),
    )


def render_dialogue_row(
    *,
    block: dict[str, Any],
    expression: str,
    character: dict[str, Any],
    resolved_image: ResolvedCharacterImage | None = None,
) -> str:
    """Render one dialogue block as one speaker/image/text row."""
    block_id = str(block["block_id"])
    speaker_id = str(block["speaker_id"])
    speaker_name = (
        resolved_image.speaker_name
        if resolved_image is not None
        else str(character.get("name", speaker_id))
    )
    if resolved_image is not None:
        image_path = resolved_image.image_path
        image_alt = resolved_image.alt
    else:
        expression_images = character.get("expression_images", {})
        expression_path = expression_images.get(expression)
        image_path = expression_path or character.get("base_image_path")
        rendered_expression = expression if expression_path else "base"
        image_alt = f"{speaker_name} - {rendered_expression}"

    if image_path:
        portrait = '<img src="{0}" alt="{1}" loading="lazy">'.format(
            escape(str(image_path), quote=True),
            escape(image_alt, quote=True),
        )
    else:
        initial = speaker_name[:1] or "?"
        portrait = (
            '<span class="portrait-placeholder" role="img" aria-label="{0}">{1}</span>'
        ).format(
            escape(
                resolved_image.alt
                if resolved_image is not None
                else f"{speaker_name} - image unavailable",
                quote=True,
            ),
            escape(initial),
        )

    return (
        '<div class="dialogue-row" data-block-id="{0}" data-expression="{1}">'
        '{2}<div class="dialogue-content"><span class="speaker">{3}</span>'
        '<p class="dialogue-line">{4}</p></div></div>'
    ).format(
        escape(block_id, quote=True),
        escape(expression, quote=True),
        portrait,
        escape(speaker_name),
        escape(str(block["text"])),
    )


def _nav_link(href: str | None, label: str, rel: str) -> str:
    if href is None:
        return ""
    return '<a href="{0}" rel="{1}">{2}</a>'.format(
        escape(href, quote=True), rel, label
    )
