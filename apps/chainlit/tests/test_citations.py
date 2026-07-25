"""Tests for config-driven citation rendering and the citation-token regex."""

from __future__ import annotations

import pytest

import citations
from config.schema import CitationConfig, FilenameRule, RagConfig, SourcesConfig


@pytest.fixture
def use_config(monkeypatch):
    """Point the citations module at a custom RagConfig."""

    def _apply(**kw) -> RagConfig:
        cfg = RagConfig(**kw)
        monkeypatch.setattr(citations, "get_config", lambda: cfg)
        citations._citation_map.cache_clear()
        return cfg

    return _apply


# --------------------------------------------------------------------------- #
# Citation-token regex (built at implementation time, not deferred)
# --------------------------------------------------------------------------- #
def test_citation_token_regex_plain_english():
    rx = citations.citation_token_regex("Source", "p.")
    m = rx.search("see Source 3: Some Title (p. 4–5) for details")
    assert m is not None
    assert m.group(1) == "3"
    assert m.group(2) == "Some Title"
    assert m.group(3) == "4–5"


def test_citation_token_regex_special_chars_token():
    # A token containing regex metacharacters must be escaped, not interpreted.
    rx = citations.citation_token_regex("Q[1]", "§")
    assert rx.search("Q[1] 2: The Title (§ 7)") is not None
    # The literal brackets are matched, not treated as a character class.
    assert rx.search("Q1 2: The Title (§ 7)") is None


def test_citation_token_regex_german_defaults():
    rx = citations.citation_token_regex("Quelle", "S.")
    m = rx.search("Quelle 12: Baustein-Titel (S. 42)")
    assert m and m.group(1) == "12" and m.group(3) == "42"


# --------------------------------------------------------------------------- #
# Segment renderer
# --------------------------------------------------------------------------- #
def test_render_generic_citation(use_config):
    use_config()  # schema defaults: segments {title} / {source_file} / p. {page}
    line = citations.render_citation_line(
        {"section_title": "Intro", "source_file": "report.pdf", "page_start": 3}
    )
    assert line == "Intro — report.pdf — p. 3"


def test_render_domain_specific_citation(use_config):
    use_config(
        citation=CitationConfig(
            token_word="Quelle",
            page_abbr="S.",
            separator=" | ",
            segments=["Modul {baustein_id}", "{baustein_titel}", "Anforderung {anforderung_id}", "S. {page}"],
            extra_fields=["baustein_id", "baustein_titel", "anforderung_id"],
        )
    )
    payload = {
        "baustein_id": "APP.1.1",
        "baustein_titel": "Office-Produkte",
        "anforderung_id": "APP.1.1.A1",
        "page_start": 42,
        "file": "IT_Grundschutz_Kompendium_Edition2023.pdf",
    }
    assert (
        citations.render_citation_line(payload)
        == "Modul APP.1.1 | Office-Produkte | Anforderung APP.1.1.A1 | S. 42"
    )


def test_segment_dropped_when_field_missing(use_config):
    use_config(
        citation=CitationConfig(
            separator=" | ",
            segments=["Modul {baustein_id}", "{title}", "S. {page}"],
            extra_fields=["baustein_id"],
        )
    )
    # No baustein_id and no page -> only the title segment survives.
    line = citations.render_citation_line({"title": "Elementare Gefährdung", "source_file": "x.pdf"})
    assert line == "Elementare Gefährdung"


def test_render_fallback_when_all_segments_empty(use_config):
    use_config(citation=CitationConfig(segments=["Modul {baustein_id}"]))
    line = citations.render_citation_line({"title": "T", "source_file": "f.pdf"})
    # Falls back to title/source_file/page_label joined by separator.
    assert "T" in line and "f.pdf" in line


# --------------------------------------------------------------------------- #
# Source-file resolution + filename_map
# --------------------------------------------------------------------------- #
def test_filename_map_rule_matches(use_config):
    use_config(
        sources=SourcesConfig(
            filename_map=[
                FilenameRule(when_field="source", matches=r"grundschutz\.json$", serve="kompendium.pdf"),
            ]
        )
    )
    assert citations.resolve_source_file({"source": "grundschutz.json"}) == "kompendium.pdf"


def test_resolve_source_file_generic_basename(use_config):
    use_config()
    assert citations.resolve_source_file({"file": "/data/docs/report.pdf"}) == "report.pdf"
    assert citations.resolve_source_file({"source": {"file": "a/b/c.txt"}}) == "c.txt"
    assert citations.resolve_source_file({}) is None
