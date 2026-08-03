"""Tests for figure encoding and for figures whose description fails.

Both guard the same past incident: the ingest step sent full-resolution PNG to the
vision model, the gateway answered HTTP 413, the exception was swallowed, and the
figure was stored as a chunk whose entire text was "Abbildung 8 (Seite 5)". In the
shipped example corpus 37 of 170 figure entries ended up like that.
"""

from __future__ import annotations

import pytest
from PIL import Image

from kb import figure_store
from kb.figure_store import pil_to_data_uri
from kb.parsers import pdf as pdf_parser


def _image(w: int = 1000, h: int = 1000) -> Image.Image:
    # Noise rather than flat colour: a solid image compresses to almost nothing and
    # would make the size assertions meaningless.
    import random

    random.seed(0)
    img = Image.new("RGB", (w, h))
    img.putdata([(random.randint(0, 255),) * 3 for _ in range(w * h)])
    return img


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #
def test_without_max_px_still_returns_png():
    """Other callers rely on the original behaviour."""
    assert pil_to_data_uri(_image(64, 64)).startswith("data:image/png;base64,")


def test_max_px_switches_to_jpeg():
    assert pil_to_data_uri(_image(64, 64), max_px=32).startswith("data:image/jpeg;base64,")


def test_encoded_figure_stays_under_the_gateway_body_limit():
    """The actual 413 regression guard. Gateways commonly cap bodies at 1 MB."""
    raw = pil_to_data_uri(_image())
    encoded = pil_to_data_uri(_image(), max_px=1536)
    assert len(raw) > 1_000_000, "test image too small to be meaningful"
    assert len(encoded) < 1_000_000
    assert len(encoded) < len(raw) / 2


def test_source_image_is_not_mutated():
    """thumbnail() resizes in place; the caller still needs the full-res image to
    persist the display PNG."""
    img = _image(800, 600)
    pil_to_data_uri(img, max_px=100)
    assert img.size == (800, 600)


def test_downscales_when_larger_than_max_px():
    small = pil_to_data_uri(_image(2000, 1000), max_px=200)
    large = pil_to_data_uri(_image(2000, 1000), max_px=2000)
    assert len(small) < len(large)


# --------------------------------------------------------------------------- #
# A failing description must not become a chunk
# --------------------------------------------------------------------------- #
class _Picture:
    def __init__(self, caption: str = "") -> None:
        self._caption = caption
        self.prov = []

    def get_image(self, _doc):  # noqa: ANN001, ANN201
        return _image(32, 32)

    def caption_text(self, _doc):  # noqa: ANN001, ANN201
        return self._caption


class _Doc:
    def __init__(self, *pictures: _Picture) -> None:
        self.pictures = list(pictures)


@pytest.fixture
def figures(monkeypatch, tmp_path):
    """Run _figure_sections with a stubbed vision call and a temp figure dir."""

    def _run(*pictures: _Picture, description: str | Exception):
        def fake_describe(*_a, **_kw):
            if isinstance(description, Exception):
                raise description
            return description

        monkeypatch.setattr(pdf_parser, "describe_image_sync", fake_describe, raising=False)
        monkeypatch.setattr("llm.describe_image_sync", fake_describe, raising=False)
        monkeypatch.setattr(figure_store, "figure_dir", lambda _cfg: tmp_path)
        from config import load_config

        cfg = load_config()
        cfg.images.mode = "describe"
        return pdf_parser._figure_sections(_Doc(*pictures), "doc", cfg, "test", 0)

    return _run


def test_failed_description_without_caption_is_skipped(figures):
    """The bug: this used to be stored as a chunk reading only "Abbildung 1"."""
    assert figures(_Picture(), description=RuntimeError("413")) == []


def test_failed_description_keeps_a_real_caption(figures):
    sections = figures(_Picture("Figure 1: survival curve"), description=RuntimeError("413"))
    assert len(sections) == 1
    assert "survival curve" in sections[0].text


def test_successful_description_is_used(figures):
    sections = figures(_Picture(), description="A bar chart comparing three methods.")
    assert len(sections) == 1
    assert "bar chart comparing" in sections[0].text


def test_describe_asks_litellm_to_retry(monkeypatch):
    """Contract test only. litellm performs the retry inside `completion`, so
    mocking `completion` to fail-then-succeed would exercise the mock, not litellm.
    What is ours to get right is passing the parameter at all: without it a single
    rate-limited call permanently loses that figure's description."""
    import litellm

    import llm as llm_mod

    seen: dict = {}

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return _Resp()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert llm_mod.describe_image_sync("data:image/jpeg;base64,x", "p", "m") == "ok"
    assert seen.get("num_retries", 0) >= 1


def test_skipping_does_not_shift_the_ids_of_later_figures(figures):
    """doc_id is derived from the picture index. If a skipped figure shifted the
    ones after it, re-ingesting would write new points instead of overwriting."""
    sections = figures(
        _Picture(), _Picture("Figure 2: has a caption"), description=RuntimeError("boom")
    )
    assert len(sections) == 1
    assert sections[0].doc_id.endswith(":fig1")
    assert sections[0].metadata["figure_index"] == 1
