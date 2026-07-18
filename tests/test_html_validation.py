import pytest

from pipeline.html_validation import HtmlOutputValidator, HtmlValidationError


def test_validator_checks_all_local_links_and_images(tmp_path):
    run_root = tmp_path / "run-001"
    chapter = run_root / "chapter-1"
    image = run_root / "assets" / "c001" / "smile face.png"
    chapter.mkdir(parents=True)
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    (run_root / "index.html").write_text(
        '<a href="chapter-1/index.html">章</a>', encoding="utf-8"
    )
    (chapter / "index.html").write_text(
        '<a href="../index.html">目次</a>'
        '<a href="section-1.html">節</a>',
        encoding="utf-8",
    )
    (chapter / "section-1.html").write_text(
        '<a href="index.html">章</a>'
        '<img src="../assets/c001/smile%20face.png" alt="葵 - smile">',
        encoding="utf-8",
    )

    report = HtmlOutputValidator(run_root).validate(
        ["index.html", "chapter-1/index.html", "chapter-1/section-1.html"]
    )

    assert report.page_count == 3
    assert report.link_count == 4
    assert report.image_count == 1


@pytest.mark.parametrize(
    ("html", "message"),
    [
        ('<a href="missing.html">missing</a>', "Broken link"),
        ('<img src="missing.png" alt="missing">', "Broken image"),
        ('<a href="../outside.html">outside</a>', "escapes the run directory"),
        ('<img src="https://example.com/image.png">', "must use a local path"),
    ],
)
def test_validator_rejects_broken_or_unsafe_references(tmp_path, html, message):
    run_root = tmp_path / "run-001"
    run_root.mkdir()
    (run_root / "index.html").write_text(html, encoding="utf-8")

    with pytest.raises(HtmlValidationError, match=message):
        HtmlOutputValidator(run_root).validate(["index.html"])


def test_validator_rejects_missing_manifest_page(tmp_path):
    run_root = tmp_path / "run-001"
    run_root.mkdir()

    with pytest.raises(HtmlValidationError, match="page does not exist"):
        HtmlOutputValidator(run_root).validate(["index.html"])
