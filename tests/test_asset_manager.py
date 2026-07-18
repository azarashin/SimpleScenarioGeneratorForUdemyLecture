from pathlib import Path

import pytest

from pipeline.asset_manager import AssetManifestError, CharacterAssetResolver
from pipeline.html_templates import render_section_page


def _assets():
    return [
        {
            "character_id": "c001",
            "base_image_path": "assets/characters/c001/base.png",
            "expression_images": {
                "smile": "assets/characters/c001/expressions/smile face.png"
            },
        }
    ]


def _profiles():
    return [{"character_id": "c001", "name": "葵"}]


def test_resolves_expression_by_character_id_with_html_relative_url():
    resolver = CharacterAssetResolver(_assets(), character_profiles=_profiles())

    resolved = resolver.resolve("c001", "smile", relative_to="chapter-1")

    assert resolved.image_path == "../assets/characters/c001/expressions/smile%20face.png"
    assert resolved.resolved_expression == "smile"
    assert resolved.alt == "葵 - smile"
    assert resolved.is_fallback is False


def test_builds_resolver_from_pipeline_data():
    resolver = CharacterAssetResolver.from_pipeline_data(
        {
            "character_image_assets": _assets(),
            "character_profiles": _profiles(),
        }
    )

    assert resolver.resolve("c001", "smile").speaker_name == "葵"


def test_unknown_expression_falls_back_to_base_image():
    resolver = CharacterAssetResolver(_assets(), character_profiles=_profiles())

    resolved = resolver.resolve("c001", "angry", relative_to="chapter-1")

    assert resolved.image_path == "../assets/characters/c001/base.png"
    assert resolved.resolved_expression == "base"
    assert resolved.alt == "葵 - base"
    assert resolved.is_fallback is True


def test_missing_character_returns_placeholder_result():
    resolver = CharacterAssetResolver(_assets(), character_profiles=_profiles())

    resolved = resolver.resolve("c999", "sad", relative_to="chapter-1")

    assert resolved.image_path is None
    assert resolved.resolved_expression is None
    assert resolved.alt == "c999 - image unavailable"
    assert resolved.is_fallback is True


def test_file_verification_falls_back_when_expression_file_is_missing(tmp_path: Path):
    base = tmp_path / "assets" / "characters" / "c001" / "base.png"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"base")
    resolver = CharacterAssetResolver(
        _assets(),
        character_profiles=_profiles(),
        run_root=tmp_path,
        verify_files=True,
    )

    resolved = resolver.resolve("c001", "smile", relative_to="chapter-1")

    assert resolved.image_path == "../assets/characters/c001/base.png"
    assert resolved.resolved_expression == "base"


@pytest.mark.parametrize("unsafe", ["../outside.png", "/absolute.png", "bad\\path.png"])
def test_rejects_unsafe_asset_paths(unsafe: str):
    assets = _assets()
    assets[0]["base_image_path"] = unsafe

    with pytest.raises(AssetManifestError, match="safe relative path"):
        CharacterAssetResolver(assets)


def test_rejects_duplicate_character_ids():
    with pytest.raises(AssetManifestError, match="duplicate character_id"):
        CharacterAssetResolver([*_assets(), *_assets()])


def test_section_renderer_uses_asset_resolver():
    resolver = CharacterAssetResolver(_assets(), character_profiles=_profiles())
    chapter = {
        "chapter_no": 1,
        "chapter_title": "出会い",
        "chapter_goal": "協力する",
        "sections": [{"section_no": 1, "section_title": "雨"}],
    }
    section = {
        "chapter_no": 1,
        "section_no": 1,
        "section_title": "雨",
        "narrative_blocks": [
            {
                "block_id": "b1",
                "type": "dialogue",
                "text": "行こう。",
                "speaker_id": "c001",
            }
        ],
    }

    html = render_section_page(
        work_title="物語",
        chapter=chapter,
        section=section,
        dialogue_tags=[{"block_id": "b1", "expression": "smile"}],
        asset_resolver=resolver,
    )

    assert 'src="../assets/characters/c001/expressions/smile%20face.png"' in html
    assert 'alt="葵 - smile"' in html
