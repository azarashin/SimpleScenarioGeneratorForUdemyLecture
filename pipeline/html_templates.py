from __future__ import annotations

from html import escape
from pathlib import Path
from string import Template
from typing import Any, Iterable


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _template(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def render_chapter_page(
    *,
    work_title: str,
    chapter: dict[str, Any],
    index_href: str = "../index.html",
) -> str:
    """Render one chapter landing page from a scenario-outline chapter."""
    chapter_no = int(chapter["chapter_no"])
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
        index_href=escape(index_href, quote=True),
    )


def render_section_page(
    *,
    work_title: str,
    chapter: dict[str, Any],
    section: dict[str, Any],
    dialogue_tags: Iterable[dict[str, Any]] = (),
    characters: dict[str, dict[str, str]] | None = None,
    previous_href: str | None = None,
    next_href: str | None = None,
    chapter_href: str = "index.html",
    index_href: str = "../index.html",
) -> str:
    """Render a section page; all user-authored text and attributes are escaped."""
    tag_by_block = {str(tag["block_id"]): tag for tag in dialogue_tags}
    character_map = characters or {}
    blocks = []
    for block in section["narrative_blocks"]:
        text = escape(str(block["text"]))
        if block["type"] == "narration":
            blocks.append(f'<p class="narration">{text}</p>')
            continue

        speaker_id = str(block["speaker_id"])
        character = character_map.get(speaker_id, {})
        speaker_name = str(character.get("name", speaker_id))
        tag = tag_by_block.get(str(block["block_id"]), {})
        expression = str(tag.get("expression", "base"))
        expression_images = character.get("expression_images", {})
        image_path = expression_images.get(expression) or character.get("base_image_path")
        image = ""
        if image_path:
            image = '<img src="{0}" alt="{1}">'.format(
                escape(str(image_path), quote=True),
                escape(
                    f"{speaker_name} - {expression if expression_images.get(expression) else 'base'}",
                    quote=True,
                ),
            )
        blocks.append(
            '<section class="dialogue">{0}<div><span class="speaker">{1}</span>'
            '<p>{2}</p></div></section>'.format(image, escape(speaker_name), text)
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


def _nav_link(href: str | None, label: str, rel: str) -> str:
    if href is None:
        return ""
    return '<a href="{0}" rel="{1}">{2}</a>'.format(
        escape(href, quote=True), rel, label
    )
