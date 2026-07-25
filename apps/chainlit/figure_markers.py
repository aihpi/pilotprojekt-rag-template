"""Inline figure placement via ``{{ABB:...}}`` markers.

Problem: Chainlit renders every ``display="inline"`` element in a grid *below* the
answer, so a figure never sits next to the paragraph discussing it. Solution: each
retrieved figure is advertised in the retrieval context with a marker token; the
model copies that token on its own line before describing the figure; this module
swaps the marker for a markdown image so it renders at exactly that position.

Why a citation number cannot be the identifier: ``rag_tool.build_context``
restarts at ``[1]`` for every tool call, and the user-visible "Quelle N" is a
session catalog id rewritten late in ``app._align_aliases_to_source_ids``. The
marker payload is therefore the figure's stored image filename, which is stable
and unique per figure — and needs no re-ingest, since chunk text is untouched.

Why curly braces: ``[ABB:x]`` is swallowed by ``app._inject_named_source_refs``
(it looks like a named source reference) and ``<ABB:x>`` is a valid CommonMark
autolink. ``{{...}}`` has no meaning in CommonMark/GFM and no transform in the
answer post-processing chain matches ``{``, ``}`` or ``ABB``.

Pure stdlib at import time (no chainlit / rag_tool / app / config), so it is unit
testable in isolation and free of import cycles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import quote

FIGURE_URL_PREFIX = "/sources/figure/"
MARKER_CONTEXT_LABEL = "Abbildungs-Marker"
"""Label used both when advertising a marker in the context and in the prompt
instruction — a module constant so the two can never drift apart."""


# --------------------------------------------------------------------------- #
# Token
# --------------------------------------------------------------------------- #
def figure_url(file_name: str) -> str:
    """Public URL for a stored figure (percent-encoded, so no character in the
    filename can break out of a markdown link)."""
    return f"{FIGURE_URL_PREFIX}{quote(file_name, safe='')}"


def marker_key(image_path: str) -> str:
    """The marker payload for an ``image_path`` (filename without ``.png``)."""
    if image_path.lower().endswith(".png"):
        return image_path[:-4]
    return image_path


def figure_marker_token(image_path: str) -> str:
    return "{{ABB:" + marker_key(image_path) + "}}"


def figure_marker_for_metadata(
    metadata: dict[str, Any] | None, *, exists: Callable[[str], bool] | None = None
) -> str | None:
    """Marker token for a chunk's metadata, or None when it is not a figure or its
    image is gone (never advertise a marker we cannot render)."""
    metadata = metadata or {}
    if not metadata.get("is_figure"):
        return None
    image_path = metadata.get("image_path")
    if not isinstance(image_path, str) or not image_path:
        return None
    if exists is not None and not exists(image_path):
        return None
    return figure_marker_token(image_path)


# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #
# Canonical double-brace form (separator optional — unambiguous).
_MARKER_DOUBLE_RE = re.compile(
    r"[`*_]{0,3}\{\{[ \t]*ABB[ \t]*[:=\-]?[ \t]*(?P<payload>[^{}\n]{0,200}?)[ \t]*\}\}[`*_]{0,3}",
    re.IGNORECASE,
)
# Tolerated single-brace / bracket / paren forms. The separator is MANDATORY here
# so a legitimate "[Abbildung 1]" can never match.
_MARKER_LOOSE_RE = re.compile(
    r"[`*_]{0,3}[\{\[\(]{1,2}[ \t]*ABB[ \t]*[:=\-][ \t]*"
    r"(?P<payload>[^{}\[\]()\n]{0,200}?)[ \t]*[\}\]\)]{1,2}[`*_]{0,3}",
    re.IGNORECASE,
)
_INLINE_FIGURE_IMG_RE = re.compile(
    r"!\[[^\]\n]*\]\(" + re.escape(FIGURE_URL_PREFIX) + r"[^)\s]*\)"
)
_ITALIC_LINE_RE = re.compile(r"^\*[^*\n]+\*$")


def has_figure_marker(text: str) -> bool:
    if not text:
        return False
    return bool(_MARKER_DOUBLE_RE.search(text) or _MARKER_LOOSE_RE.search(text))


def normalize_figure_markers(text: str) -> str:
    """Rewrite every tolerated marker spelling to canonical ``{{ABB:payload}}``.

    Must run BEFORE ``app._inject_named_source_refs``, which would otherwise
    replace a bracket-form marker with a "Quelle N:" alias."""
    if not text:
        return text

    def _canon(match: re.Match[str]) -> str:
        return "{{ABB:" + match.group("payload").strip() + "}}"

    text = _MARKER_DOUBLE_RE.sub(_canon, text)
    return _MARKER_LOOSE_RE.sub(_canon, text)


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #
def figure_display_name(metadata: dict[str, Any] | None) -> str:
    """Element name for a figure — mirrors what ``_build_inline_figure_elements``
    uses, so inlined figures can be excluded from the element grid reliably."""
    metadata = metadata or {}
    title = metadata.get("section_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    index = metadata.get("figure_index")
    number = index + 1 if isinstance(index, int) else 1
    return f"Abbildung {number}"


@dataclass(frozen=True)
class FigureCandidate:
    image_path: str          # "Schmidt_2022_SciReports__fig1.png"
    key: str                 # "Schmidt_2022_SciReports__fig1"
    display_name: str        # element name (for grid exclusion)
    caption: str | None      # section_title, when it is a real caption
    ordinal: int             # 1-based position among retrieved figures
    figure_number: int | None  # figure_index + 1 (the "Abbildung N" in chunk text)
    url: str


def build_figure_candidates(
    results: Iterable[Any] | None,
    *,
    base: Any = None,
    exists: Callable[[str], bool] | None = None,
) -> list[FigureCandidate]:
    """Resolvable figures among ``results`` (kept in ``last_results`` order).

    ``exists`` is injectable so unit tests need no filesystem; by default it
    checks the configured figure directory."""
    if exists is None:
        from config import get_config
        from kb.figure_store import figure_dir, resolve_figure_path

        figure_base = base if base is not None else figure_dir(get_config())

        def exists(name: str) -> bool:  # noqa: F811
            return resolve_figure_path(name, figure_base) is not None

    out: list[FigureCandidate] = []
    seen: set[str] = set()
    for result in results or []:
        metadata = getattr(result, "metadata", None) or {}
        if not metadata.get("is_figure"):
            continue
        image_path = metadata.get("image_path")
        if not isinstance(image_path, str) or not image_path or image_path in seen:
            continue
        if not exists(image_path):
            continue
        seen.add(image_path)
        index = metadata.get("figure_index")
        caption = metadata.get("section_title")
        out.append(
            FigureCandidate(
                image_path=image_path,
                key=marker_key(image_path),
                display_name=figure_display_name(metadata),
                caption=caption.strip() if isinstance(caption, str) and caption.strip() else None,
                ordinal=len(out) + 1,
                figure_number=index + 1 if isinstance(index, int) else None,
                url=figure_url(image_path),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #
_TOKEN_SPLIT_RE = re.compile(r"[^0-9a-zäöüß]+")
_LABEL_PREFIX_RE = re.compile(
    r"^(?:abbildungs?[-\s]?marker|marker|abb(?:ildung)?)[ \t]*[:=][ \t]*", re.IGNORECASE
)
_ORDINAL_LABEL_RE = re.compile(
    r"(?P<word>abb|abbildung|fig|figure)[ _\-]*(?P<number>\d+)", re.IGNORECASE
)


def _collapse(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _tokens(value: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT_RE.split(value.lower()) if t}


def _normalize_payload(raw: str) -> str:
    value = (raw or "").strip().strip("`\"'*_ \t")
    value = _LABEL_PREFIX_RE.sub("", value).strip().strip("`\"'*_ \t")
    if value.lower().endswith(".png"):
        value = value[:-4]
    return value


def resolve_marker(
    raw_payload: str, candidates: list[FigureCandidate]
) -> FigureCandidate | None:
    """Best-effort resolution of a marker payload to one figure.

    Every fuzzy tier requires a UNIQUE winner — showing the wrong figure is worse
    than showing none (and "none" is exactly the configured fallback: the figure
    stays in the element grid below the answer)."""
    if not candidates:
        return None
    payload = _normalize_payload(raw_payload)
    if not payload:
        return candidates[0] if len(candidates) == 1 else None
    collapsed = _collapse(payload)

    # 1) exact match on the key or the full filename
    for attr in ("key", "image_path"):
        for candidate in candidates:
            if collapsed and collapsed == _collapse(getattr(candidate, attr)):
                return candidate

    # 2) a bare number: the paper's "Abbildung N", else the Nth retrieved figure
    if re.fullmatch(r"\d+", payload):
        number = int(payload)
        hits = [c for c in candidates if c.figure_number == number]
        if len(hits) == 1:
            return hits[0]
        if 1 <= number <= len(candidates):
            return candidates[number - 1]
        return None

    # 3) labelled ordinal: "Abbildung 2" (1-based) vs "fig1" (0-based file token)
    match = _ORDINAL_LABEL_RE.fullmatch(payload)
    if match:
        number = int(match.group("number"))
        if match.group("word").lower() in {"fig", "figure"}:
            hits = [c for c in candidates if c.key.lower().endswith(f"fig{number}")]
            if len(hits) == 1:
                return hits[0]
        hits = [c for c in candidates if c.figure_number == number]
        if len(hits) == 1:
            return hits[0]
        if 1 <= number <= len(candidates):
            return candidates[number - 1]
        return None

    # 4) containment (truncated or over-qualified stems) — unique only
    hits = [
        c
        for c in candidates
        if collapsed and (collapsed in _collapse(c.key) or _collapse(c.key) in collapsed)
    ]
    if len(hits) == 1:
        return hits[0]

    # 5) token overlap (+ figure token bonus, + caption tokens) — unique argmax only
    payload_tokens = _tokens(payload)
    best: FigureCandidate | None = None
    best_score = 0
    tied = False
    for candidate in candidates:
        score = len(payload_tokens & _tokens(candidate.key))
        if candidate.figure_number is not None and f"fig{candidate.figure_number - 1}" in payload_tokens:
            score += 2
        if candidate.caption:
            score += len(payload_tokens & {t for t in _tokens(candidate.caption) if len(t) > 3})
        if score > best_score:
            best, best_score, tied = candidate, score, False
        elif score == best_score and score > 0 and candidate is not best:
            tied = True
    if best is not None and best_score >= 1 and not tied:
        return best

    # 6) a typo'd marker can only mean the single available figure
    return candidates[0] if len(candidates) == 1 else None


# --------------------------------------------------------------------------- #
# Renderer + strippers
# --------------------------------------------------------------------------- #
def _escape_md(text: str) -> str:
    return re.sub(r"([\\`*_\[\]()])", r"\\\1", text)


def _collapse_blank_runs(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def render_figure_markers(
    text: str,
    candidates: list[FigureCandidate],
    *,
    with_caption: bool = False,
) -> tuple[str, list[FigureCandidate]]:
    """Replace resolvable markers with a markdown image block placed ABOVE the
    line the marker sat on. Returns ``(text, consumed_candidates)``.

    Alt text is intentionally empty: Chainlit substring-matches element *names*
    against the message body, so any alt text could collide with an element name
    and corrupt the markdown."""
    if not text or not has_figure_marker(text):
        return text, []
    text = normalize_figure_markers(text)
    consumed: list[FigureCandidate] = []
    used: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        matches = list(_MARKER_DOUBLE_RE.finditer(line))
        if not matches:
            out.append(line)
            continue
        blocks: list[str] = []
        for match in matches:
            candidate = resolve_marker(match.group("payload"), candidates)
            if candidate is None or candidate.image_path in used:
                continue  # unresolved or duplicate -> silently drop the marker
            used.add(candidate.image_path)
            consumed.append(candidate)
            blocks.append(f"![]({candidate.url})")
            if with_caption and candidate.caption:
                blocks.append(f"*{_escape_md(candidate.caption)}*")
        remainder = _MARKER_DOUBLE_RE.sub("", line)
        remainder = re.sub(r"[ \t]{2,}", " ", remainder).strip()
        if blocks:
            if out and out[-1].strip():
                out.append("")  # image must start its own markdown block
            out.extend(blocks)
            out.append("")  # and the following paragraph must be separate
        if remainder:
            out.append(remainder)
    return _collapse_blank_runs("\n".join(out)), consumed


def strip_figure_markers(text: str) -> str:
    """Remove every marker spelling; drop lines that become empty."""
    if not text or not has_figure_marker(text):
        return text
    text = normalize_figure_markers(text)
    out: list[str] = []
    for line in text.splitlines():
        if not _MARKER_DOUBLE_RE.search(line):
            out.append(line)
            continue
        remainder = _MARKER_DOUBLE_RE.sub("", line)
        remainder = re.sub(r"[ \t]{2,}", " ", remainder).strip()
        if remainder:
            out.append(remainder)
    return _collapse_blank_runs("\n".join(out))


def strip_inline_figure_images(text: str) -> str:
    """Remove ``![](/sources/figure/...)`` images (and an immediately following
    italic caption line), leaving other images/links untouched."""
    if not text or FIGURE_URL_PREFIX not in text:
        return text
    out: list[str] = []
    drop_italic_next = False
    for line in text.splitlines():
        stripped = line.strip()
        if drop_italic_next and _ITALIC_LINE_RE.match(stripped):
            drop_italic_next = False
            continue
        drop_italic_next = False
        if not _INLINE_FIGURE_IMG_RE.search(line):
            out.append(line)
            continue
        remainder = _INLINE_FIGURE_IMG_RE.sub("", line)
        remainder = re.sub(r"[ \t]{2,}", " ", remainder).strip()
        drop_italic_next = True
        if remainder:
            out.append(remainder)
    return _collapse_blank_runs("\n".join(out))


def sanitize_for_model(text: str) -> str:
    """Strip both markers and inlined figure images — used for anything that goes
    back into the LLM history, so the model never learns to emit image markdown."""
    return strip_figure_markers(strip_inline_figure_images(text))
