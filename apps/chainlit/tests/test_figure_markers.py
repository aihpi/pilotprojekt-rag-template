"""Tests for inline figure markers (no chainlit, no Qdrant, no filesystem)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from figure_markers import (
    FIGURE_URL_PREFIX,
    build_figure_candidates,
    figure_display_name,
    figure_marker_for_metadata,
    figure_marker_token,
    figure_url,
    has_figure_marker,
    normalize_figure_markers,
    render_figure_markers,
    resolve_marker,
    sanitize_for_model,
    strip_figure_markers,
    strip_inline_figure_images,
)


@dataclass
class FakeResult:
    metadata: dict[str, Any]
    text: str = "irrelevant"
    score: float = 1.0


def _fig(image_path: str, figure_index: int, caption: str | None = None) -> FakeResult:
    return FakeResult(
        metadata={
            "is_figure": True,
            "image_path": image_path,
            "figure_index": figure_index,
            "section_title": caption,
            "source_file": image_path.split("__")[0] + ".pdf",
        }
    )


def _candidates(*results: FakeResult):
    return build_figure_candidates(results, exists=lambda name: True)


A0 = _fig("A__fig0.png", 0, "Fluoreszenz pro Kanal")
A1 = _fig("A__fig1.png", 1, "Aufbau des Mikroskops")
B0 = _fig("B__fig0.png", 0, None)
ABC = _candidates(A0, A1, B0)


# --------------------------------------------------------------------------- #
# Token helpers
# --------------------------------------------------------------------------- #
def test_token_and_url_helpers():
    assert figure_marker_token("A__fig1.png") == "{{ABB:A__fig1}}"
    assert figure_url("A__fig1.png") == f"{FIGURE_URL_PREFIX}A__fig1.png"
    # every character that could break a markdown link is percent-encoded
    encoded = figure_url("A B(1)__fig0.png")
    assert "(" not in encoded and ")" not in encoded and " " not in encoded


def test_figure_marker_for_metadata():
    assert figure_marker_for_metadata({"is_figure": True, "image_path": "A__fig0.png"}) == "{{ABB:A__fig0}}"
    assert figure_marker_for_metadata({"image_path": "A__fig0.png"}) is None  # not a figure
    assert figure_marker_for_metadata({"is_figure": True}) is None  # no image
    assert figure_marker_for_metadata(None) is None
    # a missing PNG must never be advertised
    assert figure_marker_for_metadata(
        {"is_figure": True, "image_path": "gone.png"}, exists=lambda n: False
    ) is None


def test_figure_display_name_matches_element_naming():
    assert figure_display_name({"section_title": " Caption "}) == "Caption"
    assert figure_display_name({"figure_index": 2}) == "Abbildung 3"
    # figure_index present but None used to raise TypeError in the app helper
    assert figure_display_name({"figure_index": None}) == "Abbildung 1"


# --------------------------------------------------------------------------- #
# Normalizer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        "{{ABB:A__fig1}}",
        "{ABB:A__fig1}",
        "[ABB:A__fig1]",
        "[[ABB:A__fig1]]",
        "(ABB:A__fig1)",
        "`{{ABB:A__fig1}}`",
        "**{{ABB:A__fig1}}**",
        "{{ ABB : A__fig1 }}",
        "{{abb=A__fig1}}",
        "{{ABB-A__fig1}}",
        "{{ABB A__fig1}}",
        "{{ABB:A__fig1}",
    ],
)
def test_normalize_all_spellings(raw):
    assert normalize_figure_markers(raw) == "{{ABB:A__fig1}}"


def test_normalize_is_idempotent():
    once = normalize_figure_markers("[ABB:A__fig1]")
    assert normalize_figure_markers(once) == once


@pytest.mark.parametrize(
    "text",
    [
        "[Abbildung 1]",
        "Abbildung 1 beschreibt den Aufbau.",
        "[3]",
        "[standard_200_2.pdf, S. 2]",
        "Quelle 1: Abbildung 2 (S.4)",
        "",
    ],
)
def test_normalize_leaves_non_markers_untouched(text):
    assert normalize_figure_markers(text) == text
    assert has_figure_marker(text) is False


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    ["A__fig1", "A__fig1.png", "a__FIG1", " A__fig1 ", "`A__fig1`", "Abbildungs-Marker: A__fig1"],
)
def test_resolve_exact_variants(payload):
    assert resolve_marker(payload, ABC).image_path == "A__fig1.png"


def test_resolve_bare_ordinal_prefers_paper_figure_number():
    # figure_number 2 exists exactly once (A__fig1) -> that one, not "2nd retrieved"
    assert resolve_marker("2", ABC).image_path == "A__fig1.png"
    assert resolve_marker("99", ABC) is None


def test_resolve_labelled_ordinals():
    # "fig1" is the 0-based file token
    assert resolve_marker("fig1", ABC).image_path == "A__fig1.png"
    # "Abbildung 2" is the 1-based number printed in the chunk text
    assert resolve_marker("Abbildung 2", ABC).image_path == "A__fig1.png"


def test_resolve_refuses_to_guess_when_ambiguous():
    # "A__fig" is a prefix of two candidates -> no image rather than a wrong one
    assert resolve_marker("A__fig", ABC) is None
    # figure_number 1 belongs to both A__fig0 and B__fig0
    assert resolve_marker("1", ABC) is not None  # falls back to 1st retrieved
    assert resolve_marker("xxxxx", ABC) is None


def test_resolve_token_overlap_and_single_candidate_shortcut():
    assert resolve_marker("A fig1", ABC).image_path == "A__fig1.png"
    single = _candidates(A1)
    assert resolve_marker("total garbage", single).image_path == "A__fig1.png"
    assert resolve_marker("A__fig1", []) is None


def test_build_candidates_dedupes_and_skips_missing():
    cands = build_figure_candidates([A1, A1, B0], exists=lambda n: True)
    assert [c.image_path for c in cands] == ["A__fig1.png", "B__fig0.png"]
    assert [c.ordinal for c in cands] == [1, 2]
    assert build_figure_candidates([A1], exists=lambda n: False) == []
    # non-figure results are ignored
    assert build_figure_candidates([FakeResult(metadata={"source_file": "x.pdf"})], exists=lambda n: True) == []


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
def test_render_marker_on_own_line_puts_image_above_paragraph():
    text = "Einleitung.\n{{ABB:A__fig1}}\nAbbildung 2 zeigt den Aufbau."
    out, consumed = render_figure_markers(text, ABC)
    assert out == (
        "Einleitung.\n\n"
        "![](/sources/figure/A__fig1.png)\n\n"
        "Abbildung 2 zeigt den Aufbau."
    )
    assert [c.image_path for c in consumed] == ["A__fig1.png"]


def test_render_inline_marker_hoists_image_above_its_line():
    out, consumed = render_figure_markers("Wie {{ABB:A__fig1}} zeigt, steigt das Signal.", ABC)
    assert out == "![](/sources/figure/A__fig1.png)\n\nWie zeigt, steigt das Signal."
    assert len(consumed) == 1


def test_render_same_figure_twice_yields_one_image():
    out, consumed = render_figure_markers("{{ABB:A__fig1}}\nText.\n{{ABB:A__fig1}}\nMehr.", ABC)
    assert out.count("![](/sources/figure/A__fig1.png)") == 1
    assert len(consumed) == 1
    assert "{{ABB" not in out


def test_render_two_different_figures_in_order():
    out, consumed = render_figure_markers("{{ABB:A__fig0}}\nEins.\n{{ABB:B__fig0}}\nZwei.", ABC)
    assert [c.image_path for c in consumed] == ["A__fig0.png", "B__fig0.png"]
    assert out.index("A__fig0.png") < out.index("B__fig0.png")


def test_render_unresolvable_marker_is_dropped_silently():
    out, consumed = render_figure_markers("{{ABB:unknown_thing_xyz}}\nText.", ABC)
    assert consumed == []
    assert "{{ABB" not in out and "unknown_thing_xyz" not in out
    assert out == "Text."


def test_render_never_leaves_triple_newlines_and_is_noop_without_markers():
    out, consumed = render_figure_markers("Nur Text.\n\nZweiter Absatz.", ABC)
    assert out == "Nur Text.\n\nZweiter Absatz." and consumed == []
    out2, _ = render_figure_markers("A.\n\n{{ABB:A__fig1}}\n\nB.", ABC)
    assert "\n\n\n" not in out2


def test_render_with_caption_escapes_markdown():
    cand = _candidates(_fig("C__fig0.png", 0, "Signal [a] (b) *c*"))
    out, _ = render_figure_markers("{{ABB:C__fig0}}\nText.", cand, with_caption=True)
    assert "*Signal \\[a\\] \\(b\\) \\*c\\**" in out


def test_render_url_encoding_does_not_break_link():
    cand = _candidates(_fig("A B(1)__fig0.png", 0))
    out, consumed = render_figure_markers("{{ABB:A B(1)__fig0}}", cand)
    assert len(consumed) == 1
    assert "![](/sources/figure/A%20B%281%29__fig0.png)" in out


# --------------------------------------------------------------------------- #
# Strippers
# --------------------------------------------------------------------------- #
def test_strip_figure_markers():
    # a marker-only line disappears entirely, preserving the surrounding structure
    assert strip_figure_markers("A.\n{{ABB:x}}\nB.") == "A.\nB."
    assert strip_figure_markers("Wie {{ABB:x}} zeigt.") == "Wie zeigt."
    assert strip_figure_markers("[ABB:x]\nB.") == "B."  # tolerated spelling too
    unchanged = "Kein Marker hier.\n\nZweiter Absatz."
    assert strip_figure_markers(unchanged) == unchanged


def test_strip_inline_figure_images_is_selective():
    text = "A.\n![](/sources/figure/A__fig1.png)\nB."
    assert strip_inline_figure_images(text) == "A.\nB."
    keep = "![](/public/logo.png)\n[Quelle 1: X (S.2)](/sources/pdf/x.pdf#page=2)"
    assert strip_inline_figure_images(keep) == keep


def test_strip_inline_images_removes_following_caption_line():
    text = "![](/sources/figure/A__fig1.png)\n*Aufbau des Mikroskops*\nText."
    assert strip_inline_figure_images(text) == "Text."


def test_history_invariant_render_then_sanitize_is_clean():
    """Whatever we render for display must come back marker- and image-free for the
    LLM history, with the same prose (blank-line count may differ, since rendering
    puts the image in its own markdown block)."""
    original = "Einleitung.\n{{ABB:A__fig1}}\nAbbildung 2 zeigt den Aufbau."
    rendered, _ = render_figure_markers(original, ABC)
    cleaned = sanitize_for_model(rendered)
    assert "/sources/figure/" not in cleaned
    assert "{{ABB" not in cleaned and "ABB:" not in cleaned
    assert [ln for ln in cleaned.splitlines() if ln.strip()] == [
        ln for ln in strip_figure_markers(original).splitlines() if ln.strip()
    ]
