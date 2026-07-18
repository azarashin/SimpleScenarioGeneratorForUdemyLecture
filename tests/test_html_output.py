import pytest

from pipeline.html_output import HtmlOutputPathError, HtmlOutputWriter


def test_writer_creates_utf8_html_beneath_run_root(tmp_path):
    writer = HtmlOutputWriter(tmp_path / "output" / "run-001")

    relative = writer.write("chapter-1/section-1.html", "<p>日本語</p>")

    output = tmp_path / "output" / "run-001" / relative
    assert relative == "chapter-1/section-1.html"
    assert output.read_text(encoding="utf-8") == "<p>日本語</p>"
    assert not output.with_name("section-1.html.tmp").exists()


@pytest.mark.parametrize(
    "unsafe",
    ["../index.html", "/index.html", "C:/index.html", "chapter-1\\index.html", "data.txt"],
)
def test_writer_rejects_paths_outside_html_output_tree(tmp_path, unsafe):
    writer = HtmlOutputWriter(tmp_path / "output" / "run-001")

    with pytest.raises(HtmlOutputPathError, match="safe relative .html path"):
        writer.write(unsafe, "content")
