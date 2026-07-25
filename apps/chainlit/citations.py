"""Config-driven citation & source rendering.

Everything here derives from ``config.citation`` so the same code produces
domain-neutral citations for plain corpora and BSI-style citations for the
domain example — no hardcoded domain fields. Pure functions (regex
builder, segment renderer) are unit-tested.
"""

from __future__ import annotations

import json
import re
import string
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import get_config

_FMT = string.Formatter()


# --------------------------------------------------------------------------- #
# Source-file resolution (replaces the hardcoded _canonical_pdf_from_text)
# --------------------------------------------------------------------------- #
def _first_str(d: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def resolve_source_file(payload: dict[str, Any]) -> str | None:
    """Return the served filename for a chunk, applying declarative
    ``sources.filename_map`` rules first, then generic metadata extraction."""
    cfg = get_config()
    for rule in cfg.sources.filename_map:
        value = payload.get(rule.when_field)
        if value is None:
            continue
        sval = str(value)
        if (
            (rule.equals is not None and sval == rule.equals)
            or (rule.matches is not None and re.search(rule.matches, sval))
            or (rule.in_ is not None and sval in rule.in_)
        ):
            return rule.serve

    raw = _first_str(payload, ("source_file", "file"))
    src = payload.get("source")
    if raw is None and isinstance(src, dict):
        raw = _first_str(src, ("file", "document", "source", "title"))
    if raw is None and isinstance(src, str):
        raw = src
    if raw is None:
        raw = _first_str(payload, ("document", "title"))
    return Path(raw).name if raw else None


def resolve_page(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    page = payload.get("page")
    if isinstance(page, dict):
        start, end = page.get("start"), page.get("end")
        return (start if isinstance(start, int) else None, end if isinstance(end, int) else None)
    ps, pe = payload.get("page_start"), payload.get("page_end")
    if isinstance(ps, int) or isinstance(pe, int):
        return (ps if isinstance(ps, int) else None, pe if isinstance(pe, int) else None)
    if isinstance(page, int):
        return page, None
    return None, None


def page_label(start: int | None, end: int | None, abbr: str) -> str:
    if start is not None and end is not None and start != end:
        return f"{abbr} {start}–{end}"
    if start is not None:
        return f"{abbr} {start}"
    if end is not None:
        return f"{abbr} {end}"
    return ""


# --------------------------------------------------------------------------- #
# Bibliographic citation map (optional)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _citation_map() -> dict[str, dict[str, str]]:
    cfg = get_config()
    if not cfg.citation.map_path:
        return {}
    path = cfg.resolve_path(cfg.citation.map_path)
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _biblio_fields(payload: dict[str, Any], source_file: str | None) -> dict[str, str]:
    cmap = _citation_map()
    if not cmap:
        return {}
    doc_key = None
    document = payload.get("document")
    if isinstance(document, str):
        doc_key = document
    elif source_file:
        doc_key = Path(source_file).stem
    entry = cmap.get(doc_key or "", {})
    return {k: entry[k] for k in ("author", "year", "title", "publisher") if entry.get(k)}


# --------------------------------------------------------------------------- #
# Segment-list citation renderer
# --------------------------------------------------------------------------- #
class _Blank(dict):
    def __missing__(self, key):  # keep str.format_map from raising on stray keys
        return ""


def _nonempty(value: Any) -> bool:
    return value is not None and value != ""


def citation_fields(payload: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    source_file = resolve_source_file(payload) or ""
    start, end = resolve_page(payload)
    fields: dict[str, Any] = {
        "title": _first_str(payload, ("section_title", "title")) or "",
        "source": source_file,
        "source_file": source_file,
        "file": source_file,
        "page": start if start is not None else (end if end is not None else ""),
        "page_start": start if start is not None else "",
        "page_end": end if end is not None else "",
        "page_label": page_label(start, end, cfg.citation.page_abbr),
    }
    for key in cfg.citation.extra_fields:
        value = payload.get(key)
        if _nonempty(value):
            fields[key] = value
    fields.update(_biblio_fields(payload, source_file))
    return fields


def _segment_field_names(segment: str) -> list[str]:
    return [name for _, name, _, _ in _FMT.parse(segment) if name]


def render_citation_line(payload: dict[str, Any]) -> str:
    cfg = get_config()
    fields = citation_fields(payload)
    parts: list[str] = []
    for segment in cfg.citation.segments:
        names = _segment_field_names(segment)
        if names and any(not _nonempty(fields.get(n)) for n in names):
            continue
        rendered = segment.format_map(_Blank(fields)).strip()
        if rendered:
            parts.append(rendered)
    line = cfg.citation.separator.join(parts)
    if line:
        return line
    # Fallback so a citation is never empty.
    fallback = [
        fields["title"],
        fields["source_file"],
        fields["page_label"],
    ]
    line = cfg.citation.separator.join(p for p in fallback if p)
    return line or cfg.citation.labels.get("unknown_source", "Unknown source")


# --------------------------------------------------------------------------- #
# Clickable-citation token regex (load-bearing; shared with app.py)
# --------------------------------------------------------------------------- #
def _page_abbr_alternation() -> str:
    cfg = get_config()
    # Accept the configured abbreviation plus common legacy spellings so
    # persisted history keeps matching.
    variants = {cfg.citation.page_abbr, "S.", "Seite", "p.", "page", "P."}
    return "|".join(re.escape(v) for v in variants if v)


@lru_cache(maxsize=8)
def citation_token_regex(token_word: str, page_abbr: str) -> re.Pattern:
    """Regex matching an in-text source token, e.g. ``Quelle 3: Title (S. 4–5)``.

    Built from config so a configured token word / page abbreviation flows to
    the citation-repair logic. Cached by (token_word, page_abbr) so tests can
    exercise arbitrary tokens (including regex specials) directly.
    """
    token = re.escape(token_word)
    abbr_variants = {page_abbr, "S.", "Seite", "p.", "page"}
    abbr = "|".join(re.escape(v) for v in abbr_variants if v)
    # e.g. "Quelle 3: Some Title (S. 4–5)"
    return re.compile(
        rf"{token}\s+(\d+)\s*:\s*(.+?)\s*\((?:{abbr})\s*([\d–\-]+)\)",
        re.IGNORECASE,
    )


def default_citation_token_regex() -> re.Pattern:
    cfg = get_config()
    return citation_token_regex(cfg.citation.token_word, cfg.citation.page_abbr)
