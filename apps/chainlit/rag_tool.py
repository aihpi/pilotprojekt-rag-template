from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
)

import citations
import figure_markers as figure_markers_mod
from config import get_config
from kb.sparse import SPARSE_VECTOR, sparse_vector
from llm import embed
from settings import (
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
    SCORE_THRESHOLD,
    TOP_K,
)

if TYPE_CHECKING:
    from user_profile import UserProfile


@dataclass
class RagResult:
    text: str
    score: float
    metadata: dict[str, Any]


_client: QdrantClient | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _client


def _extract_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "chunk", "body"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_source_file(payload: dict[str, Any]) -> str | None:
    """Served filename for a chunk (config-driven; see :mod:`citations`)."""
    return citations.resolve_source_file(payload)


def extract_page(payload: dict[str, Any]) -> int | None:
    page_start = payload.get("page_start")
    if isinstance(page_start, int):
        return page_start

    page = payload.get("page")
    if isinstance(page, int):
        return page
    if isinstance(page, dict):
        start = page.get("start")
        if isinstance(start, int):
            return start
    return None


def _clean_text(text: str, max_len: int = 1200) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


# The per-collection metadata points (the embed-model sentinel and the file
# manifest) are stored with a real vector copied from the first chunk, because
# Qdrant wants one of the right size. A query similar to that chunk therefore ties
# with them: they take top result slots and are then dropped for having no text,
# which silently shortens or empties the result list. Excluding them in Qdrant
# means they never consume a slot in the first place.
_EXCLUDE_META = [FieldCondition(key="_meta", match=MatchValue(value=True))]
_META_FILTER = Filter(must_not=_EXCLUDE_META)


async def retrieve(
    query: str,
    top_k: int | None = None,
    *,
    collection: str | None = None,
    filters: dict[str, Any] | None = None,
    source_scope: str | None = None,
    standard_id: str | None = None,
    include_vectors: bool = False,
) -> list[RagResult]:
    """Retrieve documents matching the query.

    Args:
        query: Search query text
        top_k: Number of results to return
        collection: Override the configured Qdrant collection
        filters: Generic metadata filters (field -> value or list of values);
            only fields listed in ``retrieval.filterable_fields`` are applied
        source_scope: Deprecated shim, folded into ``filters``
        standard_id: Deprecated shim, folded into ``filters``
        include_vectors: If True, include embedding vectors in results (for personalization)

    Returns:
        List of RagResult objects
    """
    cfg = get_config()
    client = _get_client()
    vector = (await embed([query]))[0]
    k = top_k or TOP_K
    target = collection or QDRANT_COLLECTION

    requested = dict(filters or {})
    if source_scope:
        requested.setdefault("source_scope", source_scope)
    if standard_id:
        requested.setdefault("standard_id", standard_id)
    allowed = set(cfg.retrieval.filterable_fields)
    must: list[FieldCondition] = []
    for key, value in requested.items():
        if key not in allowed:
            continue
        if isinstance(value, (list, tuple, set)):
            must.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))
    query_filter = Filter(must=must, must_not=_EXCLUDE_META) if must else _META_FILTER

    def _query(active_filter):
        if not cfg.retrieval.hybrid:
            return client.query_points(
                collection_name=target,
                query=vector,
                limit=k,
                score_threshold=SCORE_THRESHOLD,
                with_payload=True,
                with_vectors=include_vectors,
                query_filter=active_filter,
            )
        # score_threshold belongs on the dense leg, never on the fused query:
        # RRF scores peak near 1/61, so a cosine-calibrated threshold applied to
        # the fusion result discards everything. The filter has to ride on both
        # legs too, or the _meta sentinel and manifest re-enter the candidate
        # pool (see tests/test_retrieval_meta.py).
        return client.query_points(
            collection_name=target,
            prefetch=[
                Prefetch(
                    query=vector,
                    limit=cfg.retrieval.prefetch_limit,
                    score_threshold=SCORE_THRESHOLD,
                    filter=active_filter,
                ),
                Prefetch(
                    query=sparse_vector(query),
                    using=SPARSE_VECTOR,
                    limit=cfg.retrieval.prefetch_limit,
                    filter=active_filter,
                ),
            ],
            query=FusionQuery(fusion=Fusion(cfg.retrieval.fusion)),
            limit=k,
            with_payload=True,
            with_vectors=include_vectors,
        )

    response = _query(query_filter)
    points = list(response.points or [])
    if not points and must:
        # Compatibility fallback for collections without the filtered fields.
        response = _query(_META_FILTER)
        points = list(response.points or [])

    hits: list[RagResult] = []
    for hit in points:
        payload = dict(hit.payload or {})
        text = _extract_text(payload)
        if not text:
            continue
        # Store embedding vector if requested (for personalization scoring)
        if include_vectors and hit.vector is not None:
            if isinstance(hit.vector, list):
                payload["_embedding"] = hit.vector
        hits.append(
            RagResult(
                text=_clean_text(text),
                score=float(hit.score),
                metadata=payload,
            )
        )
    return hits


async def personalized_retrieve(
    query: str,
    user_profile: "UserProfile | None",
    balance: float = 1.0,
    top_k: int | None = None,
    *,
    source_scope: str | None = None,
    standard_id: str | None = None,
) -> list[RagResult]:
    """Retrieve documents. Personalization (keyword-based filtering) has been
    removed — retrieval always uses standard semantic search.

    Keywords now only influence the system prompt ('Bezug zu Ihren Interessen'
    section), not chunk retrieval or scoring.

    Args:
        query: Search query text
        user_profile: User profile (unused for retrieval, kept for API compat)
        balance: Unused, kept for API compatibility
        top_k: Number of results to return
        source_scope: Optional filter by source scope
        standard_id: Optional filter by standard ID

    Returns:
        List of RagResult objects
    """
    return await retrieve(
        query, top_k, source_scope=source_scope, standard_id=standard_id
    )


def context_with_source(result: RagResult) -> str:
    """One retrieved chunk plus its provenance line, as the model receives it.

    Shared with :func:`build_context` so that the text a judge scores an answer
    against cannot drift from the text the model was actually given.

    That drift was a real bug. Answer scoring used to send bare ``result.text``, so
    the judge never saw which document a chunk came from — and every answer's
    closing "Die Informationen stammen aus der Quelle X (Seite 1-2)" was therefore
    unverifiable. Faithfulness docked a claim on essentially every cited answer, with
    the reason "the context does not mention the source ... as the source of the
    information", which is true only because we had removed it.
    """
    label = get_config().citation.labels.get("source", "Source")
    return f"{result.text}\n{label}: {citations.render_citation_line(result.metadata)}"


def build_context(results: list[RagResult], *, figure_markers: bool | None = None) -> str:
    """Numbered retrieval context for the model.

    For figure chunks an extra ``Abbildungs-Marker: {{ABB:...}}`` line is appended
    (unless disabled) so the model can request that image be shown above the
    paragraph describing it — see :mod:`figure_markers`. ``figure_markers=None``
    follows ``images.inline_figures`` from the config.
    """
    cfg = get_config()
    want_markers = cfg.images.inline_figures if figure_markers is None else figure_markers
    exists = None
    if want_markers and cfg.images.mode != "none":
        try:
            from kb.figure_store import figure_dir, resolve_figure_path

            base = figure_dir(cfg)
            exists = lambda name: resolve_figure_path(name, base) is not None  # noqa: E731
        except Exception:  # noqa: BLE001 — never break retrieval over the figure dir
            exists = None

    lines: list[str] = []
    for idx, result in enumerate(results, start=1):
        entry = f"[{idx}] {context_with_source(result)}"
        if exists is not None:
            token = figure_markers_mod.figure_marker_for_metadata(result.metadata, exists=exists)
            if token:
                entry = f"{entry}\n{figure_markers_mod.MARKER_CONTEXT_LABEL}: {token}"
        lines.append(entry)
    return "\n\n".join(lines)


def format_citations(results: list[RagResult]) -> str:
    lines: list[str] = []
    for idx, result in enumerate(results, start=1):
        lines.append(f"[{idx}] {citations.render_citation_line(result.metadata)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Document-level backends for the agentic tools (tools/ package).
# These scroll Qdrant directly (bypassing the semantic top-k / filterable_fields
# path) so the agent can enumerate the corpus and load whole documents.
# --------------------------------------------------------------------------- #
def _scroll_all(client, collection: str, *, scroll_filter=None, cap: int = 20000):
    points: list[Any] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            with_payload=True,
            limit=1000,
            offset=offset,
        )
        points.extend(batch)
        if offset is None or len(points) >= cap:
            break
    return points


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _section_order_key(point: Any) -> tuple[int, int, int]:
    payload = point.payload or {}
    # chunk_index disambiguates sub-chunks that share a section (semantic chunker).
    chunk_index = payload.get("chunk_index")
    chunk_index = chunk_index if isinstance(chunk_index, int) else 0
    section_index = payload.get("section_index")
    if isinstance(section_index, int):
        return (0, section_index, chunk_index)
    page_start = payload.get("page_start")
    return (1, page_start if isinstance(page_start, int) else 0, chunk_index)


# Section headings that are structural boilerplate, not a document title.
_NON_TITLE_HEADINGS = {
    "untitled section", "open", "abstract", "introduction", "references",
    "acknowledgements", "acknowledgments", "author contributions",
    "data availability", "funding", "competing interests",
    "additional information", "materials and methods", "methods",
    "results", "discussion", "conclusion", "conclusions",
    "results and discussion", "results and discussions",
    "methods dataset", "supplementary information",
}


def _looks_like_title(heading: Any) -> bool:
    if not isinstance(heading, str):
        return False
    text = re.sub(r"\s+", " ", heading).strip()
    return len(text) >= 12 and text.lower() not in _NON_TITLE_HEADINGS


async def list_documents(*, collection: str | None = None) -> list[dict[str, Any]]:
    """Enumerate the collection: one entry per distinct ``source_file`` with its
    chunk count and a rough token estimate. The best-effort ``title`` is the
    first real heading in reading order (structural headings like References /
    Acknowledgements are skipped); it is ``None`` when no descriptive heading is
    found — ``source_file`` is the reliable identifier. Navigational — no
    citations."""
    client = _get_client()
    target = collection or QDRANT_COLLECTION
    docs: dict[str, dict[str, Any]] = {}
    for point in _scroll_all(client, target):
        payload = point.payload or {}
        if payload.get("_meta"):
            continue
        source_file = payload.get("source_file") or payload.get("source")
        if not source_file:
            continue
        entry = docs.setdefault(
            source_file,
            {"source_file": source_file, "title": None, "_title_key": None, "chunks": 0, "_chars": 0},
        )
        entry["chunks"] += 1
        entry["_chars"] += len(payload.get("text") or "")
        heading = payload.get("title") or payload.get("section_title")
        if _looks_like_title(heading):
            key = _section_order_key(point)  # earliest real heading in reading order
            if entry["_title_key"] is None or key < entry["_title_key"]:
                entry["_title_key"] = key
                entry["title"] = re.sub(r"\s+", " ", heading).strip()
    out: list[dict[str, Any]] = []
    for source_file in sorted(docs):
        entry = docs[source_file]
        out.append(
            {
                "source_file": source_file,
                "title": entry["title"],
                "chunks": entry["chunks"],
                "approx_tokens": entry["_chars"] // 4,
            }
        )
    return out


async def fetch_document(
    source_file: str, *, collection: str | None = None, max_chunks: int | None = None
) -> list[RagResult]:
    """All chunks of one document in reading order (by ``section_index``), as
    RagResult items (score 1.0). Exact ``source_file`` match, with a
    whitespace/case-insensitive fallback. Capped at ``max_chunks``."""
    client = _get_client()
    target = collection or QDRANT_COLLECTION
    flt = Filter(must=[FieldCondition(key="source_file", match=MatchValue(value=source_file))])
    points = _scroll_all(client, target, scroll_filter=flt)
    if not points:
        wanted = _norm_name(source_file)
        points = [
            p
            for p in _scroll_all(client, target)
            if _norm_name(str((p.payload or {}).get("source_file") or "")) == wanted
        ]
    points.sort(key=_section_order_key)
    if max_chunks:
        points = points[:max_chunks]
    results: list[RagResult] = []
    for point in points:
        payload = dict(point.payload or {})
        if payload.get("_meta"):
            continue
        text = _extract_text(payload)
        if not text:
            continue
        results.append(RagResult(text=_clean_text(text, max_len=4000), score=1.0, metadata=payload))
    return results


async def expand_context(
    source_file: str, section_index: int, *, window: int = 1, collection: str | None = None
) -> list[RagResult]:
    """Neighboring chunks around ``section_index`` (± ``window``) within one
    document, in order. Degrades to the exact-section chunk if section indices
    are absent."""
    client = _get_client()
    target = collection or QDRANT_COLLECTION
    flt = Filter(must=[FieldCondition(key="source_file", match=MatchValue(value=source_file))])
    points = _scroll_all(client, target, scroll_filter=flt)
    low, high = section_index - window, section_index + window
    selected = [
        p
        for p in points
        if isinstance((p.payload or {}).get("section_index"), int)
        and low <= (p.payload or {})["section_index"] <= high
    ]
    if not selected:
        selected = [p for p in points if (p.payload or {}).get("section_index") == section_index]
    selected.sort(key=_section_order_key)
    results: list[RagResult] = []
    for point in selected:
        payload = dict(point.payload or {})
        if payload.get("_meta"):
            continue
        text = _extract_text(payload)
        if not text:
            continue
        results.append(RagResult(text=_clean_text(text), score=1.0, metadata=payload))
    return results


async def verify_claim(
    claim: str,
    *,
    filters: dict[str, Any] | None = None,
    collection: str | None = None,
    top_k: int | None = None,
) -> tuple[list[RagResult], bool]:
    """Re-retrieve for a drafted claim; return (evidence, supported). ``supported``
    is a soft signal — the model still reads the evidence."""
    results = await retrieve(claim, top_k or TOP_K, filters=filters, collection=collection)
    floor = max(SCORE_THRESHOLD, 0.3)
    supported = any(r.score >= floor for r in results)
    return results, supported
