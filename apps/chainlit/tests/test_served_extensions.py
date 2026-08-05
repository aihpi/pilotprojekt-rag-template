"""sources.served_extensions has to actually gate what /sources serves.

It was dead: shipped set in three configs, documented in the README and both
adding-data pages, asserted in a test, and read by nothing. The route hardcoded
".pdf", so adding ".md" and expecting Markdown sources to open gave a silent 404.
"""

import mimetypes

import app as chainlit_app


def _with_extensions(monkeypatch, tmp_path, extensions):
    cfg = chainlit_app.get_config()
    monkeypatch.setattr(cfg.sources, "served_extensions", extensions)
    monkeypatch.setattr(chainlit_app, "DATA_RAW_DIR", tmp_path)
    return cfg


def test_a_listed_extension_is_served(monkeypatch, tmp_path):
    (tmp_path / "notes.md").write_text("# hello", encoding="utf-8")
    _with_extensions(monkeypatch, tmp_path, [".pdf", ".md"])

    assert chainlit_app._resolve_source_pdf_path("notes.md") == tmp_path / "notes.md"


def test_an_unlisted_extension_is_refused(monkeypatch, tmp_path):
    (tmp_path / "secrets.env").write_text("KEY=1", encoding="utf-8")
    _with_extensions(monkeypatch, tmp_path, [".pdf", ".md"])

    assert chainlit_app._resolve_source_pdf_path("secrets.env") is None


def test_extensions_are_normalised(monkeypatch, tmp_path):
    """A config may write them without a dot, or in capitals."""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    _with_extensions(monkeypatch, tmp_path, ["md", ".PDF"])

    assert chainlit_app._served_suffixes() == {".md", ".pdf"}
    assert chainlit_app._resolve_source_pdf_path("a.md") is not None


def test_traversal_is_still_refused(monkeypatch, tmp_path):
    """The gate that matters most must survive the change."""
    _with_extensions(monkeypatch, tmp_path, [".md"])

    for name in ("../app.py", "sub/a.md", "/etc/passwd"):
        assert chainlit_app._resolve_source_pdf_path(name) is None


def test_the_media_type_matches_the_file():
    """Everything used to be served as application/pdf."""
    assert mimetypes.guess_type("a.md")[0] == "text/markdown"
    assert mimetypes.guess_type("a.pdf")[0] == "application/pdf"
