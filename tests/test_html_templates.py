import pytest

from pipeline.html_templates import (
    build_chapter_navigation,
    build_section_navigation,
    render_chapter_page,
    render_dialogue_row,
    render_index_page,
    render_section_page,
)


def _chapter():
    return {
        "chapter_no": 1,
        "chapter_title": "出会い & 選択",
        "chapter_goal": "二人が協力を決める",
        "sections": [
            {"section_no": 1, "section_title": "雨の駅"},
            {"section_no": 2, "section_title": "小さな約束"},
        ],
    }


def test_chapter_template_lists_sections_and_escapes_content():
    html = render_chapter_page(work_title="物語 <script>", chapter=_chapter())

    assert '<meta charset="utf-8">' in html
    assert "第1章" in html
    assert "出会い &amp; 選択" in html
    assert 'href="section-1.html"' in html
    assert 'href="section-2.html"' in html
    assert "<script>" not in html


def test_index_template_links_every_chapter_and_section():
    html = render_index_page(outline=_outline())

    assert 'aria-label="目次"' in html
    assert 'href="chapter-1/index.html"' in html
    assert 'href="chapter-1/section-1.html"' in html
    assert 'href="chapter-1/section-2.html"' in html
    assert 'href="chapter-2/index.html"' in html
    assert 'href="chapter-2/section-1.html"' in html


def test_chapter_template_builds_previous_next_and_index_links():
    html = render_chapter_page(
        work_title="物語", chapter=_outline()["chapters"][1], outline=_outline()
    )

    assert 'href="../chapter-1/index.html" rel="previous"' in html
    assert 'href="../index.html">目次へ戻る</a>' in html
    assert 'rel="next"' not in html


def test_chapter_navigation_handles_boundaries():
    first = build_chapter_navigation(_outline(), chapter_no=1)
    last = build_chapter_navigation(_outline(), chapter_no=2)

    assert first.previous_href is None
    assert first.next_href == "../chapter-2/index.html"
    assert last.previous_href == "../chapter-1/index.html"
    assert last.next_href is None


def test_section_template_renders_blocks_images_and_navigation():
    section = {
        "chapter_no": 1,
        "section_no": 1,
        "section_title": "雨の駅",
        "narrative_blocks": [
            {"block_id": "b1", "type": "narration", "text": "雨が降る。", "speaker_id": None},
            {"block_id": "b2", "type": "dialogue", "text": "行こう。", "speaker_id": "c001"},
        ],
    }
    html = render_section_page(
        work_title="物語",
        chapter=_chapter(),
        section=section,
        dialogue_tags=[{"block_id": "b2", "expression": "smile"}],
        characters={
            "c001": {
                "name": "葵",
                "base_image_path": "../assets/c001/base.png",
                "expression_images": {"smile": "../assets/c001/smile.png"},
            }
        },
        previous_href="section-0.html",
        next_href="section-2.html",
    )

    assert "雨が降る。" in html
    assert "行こう。" in html
    assert "葵 - smile" in html
    assert '../assets/c001/smile.png' in html
    assert html.count('class="dialogue-row"') == 1
    assert 'data-block-id="b2"' in html
    assert 'class="speaker">葵</span>' in html
    assert 'class="dialogue-line">行こう。</p>' in html
    assert 'rel="previous"' in html
    assert 'rel="next"' in html


def test_section_template_falls_back_to_base_image():
    section = {
        "chapter_no": 1,
        "section_no": 1,
        "section_title": "雨の駅",
        "narrative_blocks": [
            {"block_id": "b2", "type": "dialogue", "text": "行こう。", "speaker_id": "c001"},
        ],
    }
    html = render_section_page(
        work_title="物語",
        chapter=_chapter(),
        section=section,
        dialogue_tags=[{"block_id": "b2", "expression": "angry"}],
        characters={"c001": {"name": "葵", "base_image_path": "base.png"}},
    )

    assert 'src="base.png"' in html
    assert 'alt="葵 - base"' in html


def test_dialogue_row_uses_accessible_placeholder_when_no_image_exists():
    html = render_dialogue_row(
        block={
            "block_id": "b&1",
            "type": "dialogue",
            "text": "<待って>",
            "speaker_id": "c001",
        },
        expression="surprised",
        character={"name": "葵"},
    )

    assert 'class="dialogue-row"' in html
    assert 'data-block-id="b&amp;1"' in html
    assert 'data-expression="surprised"' in html
    assert 'class="portrait-placeholder"' in html
    assert 'aria-label="葵 - image unavailable"' in html
    assert '&lt;待って&gt;' in html


def test_section_uses_expression_tag_for_the_current_section_only():
    section = {
        "chapter_no": 1,
        "section_no": 1,
        "section_title": "雨の駅",
        "narrative_blocks": [
            {"block_id": "b2", "type": "dialogue", "text": "行こう。", "speaker_id": "c001"}
        ],
    }
    html = render_section_page(
        work_title="物語",
        chapter=_chapter(),
        section=section,
        dialogue_tags=[
            {"chapter_no": 2, "section_no": 1, "block_id": "b2", "expression": "angry"},
            {"chapter_no": 1, "section_no": 1, "block_id": "b2", "expression": "smile"},
        ],
        characters={
            "c001": {
                "name": "葵",
                "base_image_path": "base.png",
                "expression_images": {"smile": "smile.png", "angry": "angry.png"},
            }
        },
    )

    assert 'src="smile.png"' in html
    assert 'src="angry.png"' not in html


def _outline():
    chapter_1 = _chapter()
    chapter_2 = {
        "chapter_no": 2,
        "chapter_title": "旅立ち",
        "chapter_goal": "新しい場所へ進む",
        "sections": [{"section_no": 1, "section_title": "朝"}],
    }
    return {"title": "物語", "chapters": [chapter_1, chapter_2]}


def test_navigation_links_sections_across_chapter_boundaries():
    navigation = build_section_navigation(_outline(), chapter_no=1, section_no=2)

    assert navigation.previous_href == "section-1.html"
    assert navigation.next_href == "../chapter-2/section-1.html"
    assert navigation.chapter_href == "index.html"
    assert navigation.index_href == "../index.html"


def test_navigation_omits_links_at_story_boundaries():
    first = build_section_navigation(_outline(), chapter_no=1, section_no=1)
    last = build_section_navigation(_outline(), chapter_no=2, section_no=1)

    assert first.previous_href is None
    assert first.next_href == "section-2.html"
    assert last.previous_href == "../chapter-1/section-2.html"
    assert last.next_href is None


def test_navigation_rejects_unknown_section():
    with pytest.raises(ValueError, match="Section is not present in outline"):
        build_section_navigation(_outline(), chapter_no=9, section_no=9)


def test_section_template_can_build_navigation_from_outline():
    section = {
        "chapter_no": 1,
        "section_no": 2,
        "section_title": "小さな約束",
        "narrative_blocks": [
            {"block_id": "b1", "type": "narration", "text": "朝になった。", "speaker_id": None}
        ],
    }
    html = render_section_page(
        work_title="物語",
        chapter=_chapter(),
        section=section,
        outline=_outline(),
    )

    assert 'href="section-1.html" rel="previous"' in html
    assert 'href="../chapter-2/section-1.html" rel="next"' in html
    assert 'href="index.html">章トップ</a>' in html
    assert 'href="../index.html">物語</a>' in html
