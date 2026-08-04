"""`pdf_options.ocr: true` must fail immediately, not minutes into a conversion.

The shipped Docker image installs no apt packages on purpose (it went from
5.97 GB to 1.6 GB that way), so it carries no OCR engine. Docling's own error is
clear but only surfaces once the converter has started, and it cannot know about
the Docker situation or that `ocr: false` reads most PDFs perfectly well.

`shutil.which` is monkeypatched in both directions so the result does not depend
on whether the machine running the tests happens to have tesseract.
"""

from __future__ import annotations

import shutil

import pytest

from kb.parsers import pdf as pdf_parser


def test_missing_tesseract_raises_an_actionable_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    with pytest.raises(RuntimeError) as excinfo:
        pdf_parser._build_ocr_options("tesseract", ["eng"], False, enabled=True)

    message = str(excinfo.value)
    assert "pdf_options.ocr" in message
    assert "tesseract-ocr" in message, "must name the package to install"
    assert "ocr: false" in message, "must mention that most PDFs need no OCR"


def test_ocr_disabled_does_not_need_the_binary(monkeypatch):
    """The regression this nearly shipped with.

    PdfPipelineOptions always wants an ocr_options object, so the parser builds one
    on every run and `do_ocr` is what switches OCR on. Checking for the binary
    unconditionally broke every ordinary ingest, which never OCRs anything.
    """
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    options = pdf_parser._build_ocr_options("tesseract", ["eng"], False, enabled=False)

    assert options.lang == ["eng"]


def test_present_tesseract_builds_options(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/tesseract")

    options = pdf_parser._build_ocr_options("tesseract", ["eng", "deu"], True, enabled=True)

    assert options.lang == ["eng", "deu"]
    assert options.force_full_page_ocr is True


def test_mac_engine_is_not_checked_for_tesseract(monkeypatch):
    """The macOS engine is built into the OS; looking for a binary would be wrong."""
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    options = pdf_parser._build_ocr_options("mac", ["eng"], False)

    assert options.lang == ["eng"]


def test_the_configured_command_is_the_one_checked(monkeypatch):
    """Respect tesseract_cmd, so overriding the binary cannot trigger a false alarm."""
    looked_for: list[str] = []

    def fake_which(cmd):
        looked_for.append(cmd)
        return "/opt/custom/tesseract"

    monkeypatch.setattr(shutil, "which", fake_which)
    pdf_parser._build_ocr_options("tesseract", ["eng"], False, enabled=True)

    from docling.datamodel.pipeline_options import TesseractOcrOptions

    expected = getattr(TesseractOcrOptions(lang=["eng"]), "tesseract_cmd", None) or "tesseract"
    assert looked_for == [expected]
