from pipeline.html_templates import render_chapter_page, render_section_page


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
