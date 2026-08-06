"""Agentic tool registry, schemas, and handler guard paths.

No Qdrant and no gateway: the handlers import ``rag_tool`` inside their bodies
(the import-cycle rule in ``tools/base.py``), so patching attributes on the
module is enough to stand in for retrieval. ``build_context`` and
``format_citations`` are left real — they are pure given the config, and running
them here means a change to the payload shape shows up as a test failure.
"""

from __future__ import annotations

import asyncio

import pytest

import rag_tool
from config.schema import RagConfig
from tools import (
    TOOL_REGISTRY,
    ToolContext,
    build_openai_tools,
    clamp_top_k,
    enabled_tool_ids,
    get_tool,
)


def _results(n: int) -> list[rag_tool.RagResult]:
    return [
        rag_tool.RagResult(
            text=f"chunk {i}",
            score=1.0 - i / 100,
            metadata={"source_file": f"doc{i}.pdf", "section_index": i},
        )
        for i in range(n)
    ]


def _run(tool_id: str, args: dict, ctx: ToolContext | None = None):
    return asyncio.run(get_tool(tool_id).handler(args, ctx or ToolContext()))


# --------------------------------------------------------------------------- #
# clamp_top_k — the model sends whatever it likes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 5),        # omitted -> default
        ("junk", 5),      # unparseable -> default
        (-4, 1),          # floored, never zero
        ("3", 3),         # numeric string is accepted
        (99, 8),          # capped at maximum
    ],
)
def test_clamp_top_k(raw, expected):
    assert clamp_top_k(raw, default=5, maximum=8) == expected


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_empty_enabled_list_falls_back_to_search():
    """An instance that switches every tool off must still be able to retrieve."""
    cfg = RagConfig()
    cfg.tools.enabled = []
    assert enabled_tool_ids(cfg) == ["search"]


def test_unknown_tool_id_names_the_registered_ones():
    """A typo in tools.enabled is a config error — the message has to be actionable."""
    with pytest.raises(KeyError) as excinfo:
        get_tool("serach")
    message = str(excinfo.value)
    assert "serach" in message
    for tool_id in TOOL_REGISTRY:
        assert tool_id in message


@pytest.mark.parametrize("tool_id", sorted(TOOL_REGISTRY))
def test_every_tool_builds_a_valid_openai_schema(tool_id):
    """Structural contract for the schema passed to chat(tools=...)."""
    schema = get_tool(tool_id).build_schema(RagConfig())

    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"], "a tool with no function name cannot be routed"
    assert function["description"]

    params = function["parameters"]
    assert params["type"] == "object"
    properties = params["properties"]
    for name in params.get("required", []):
        assert name in properties, f"required '{name}' is not declared in properties"
    for name, spec in properties.items():
        assert spec.get("type"), f"property '{name}' has no type"
        assert spec.get("description"), f"property '{name}' has no description"


def test_router_keys_on_the_function_name_not_the_tool_id():
    """search's function name comes from `tool.name`. Renaming it in config must
    not break dispatch — the router is keyed by what the model actually sends."""
    cfg = RagConfig()
    cfg.tool.name = "custom_search"
    cfg.tools.enabled = ["search", "list_documents"]

    schemas, by_function_name = build_openai_tools(cfg)

    assert [s["function"]["name"] for s in schemas] == ["custom_search", "list_documents"]
    assert by_function_name["custom_search"] is get_tool("search")
    assert "search" not in by_function_name, "the registry id is not what the model sends"


# --------------------------------------------------------------------------- #
# Descriptions: language default, then explicit override
# --------------------------------------------------------------------------- #
def test_description_follows_language_and_yields_to_an_override():
    cfg = RagConfig()

    cfg.language = "en"
    english = get_tool("list_documents").build_schema(cfg)["function"]["description"]
    cfg.language = "de"
    german = get_tool("list_documents").build_schema(cfg)["function"]["description"]
    assert english != german
    assert "knowledge base" in english
    assert "Wissensbasis" in german

    cfg.language = "de-DE"  # a region suffix is as plausible in a config as plain "de"
    assert get_tool("list_documents").build_schema(cfg)["function"]["description"] == german

    cfg.tools.descriptions = {"list_documents": "Nur diese eine."}
    assert (
        get_tool("list_documents").build_schema(cfg)["function"]["description"]
        == "Nur diese eine."
    )


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
@pytest.fixture
def captured_retrieve(monkeypatch):
    """Stand in for the Qdrant round-trip, recording what the handler asked for."""
    seen: dict = {}

    async def fake_retrieve(query, top_k, filters=None, collection=None):
        seen.update(query=query, top_k=top_k, filters=filters, collection=collection)
        return _results(2)

    monkeypatch.setattr(rag_tool, "retrieve", fake_retrieve)
    return seen


def test_search_falls_back_to_the_user_message_on_an_empty_query(captured_retrieve):
    """The model sometimes calls the tool with no query at all."""
    ctx = ToolContext(query_fallback="what does the report say?")
    result = _run("search", {"query": ""}, ctx)

    assert captured_retrieve["query"] == "what does the report say?"
    assert result.payload["query"] == "what does the report say?"
    assert len(result.results) == 2


def test_search_clamps_top_k_to_the_context_maximum(captured_retrieve):
    _run("search", {"query": "q", "top_k": 500}, ToolContext(default_top_k=5, max_top_k=8))
    assert captured_retrieve["top_k"] == 8


def test_search_scopes_by_document_without_mutating_the_shared_filters(captured_retrieve):
    """ctx is reused across every tool call in a turn. A handler that writes into
    ctx.filters would silently scope all later calls to the first document."""
    ctx = ToolContext(filters={"source_scope": "public"})
    _run("search", {"query": "q", "document": "report.pdf"}, ctx)

    assert captured_retrieve["filters"] == {
        "source_scope": "public",
        "source_file": "report.pdf",
    }
    assert ctx.filters == {"source_scope": "public"}, "ctx.filters was mutated"


def test_search_payload_keeps_its_back_compat_shape(captured_retrieve):
    """Search-only instances downstream still read exactly these three keys."""
    result = _run("search", {"query": "q"})
    assert set(result.payload) == {"query", "context", "citations"}


# --------------------------------------------------------------------------- #
# expand_context
# --------------------------------------------------------------------------- #
def test_expand_context_reports_missing_arguments_instead_of_raising():
    """A handler exception aborts the turn; an error payload lets the model retry."""
    for args in ({}, {"source_file": "d.pdf"}, {"section_index": 3}):
        result = _run("expand_context", args)
        assert "error" in result.payload, f"{args} should be reported, not raised"
        assert result.results == []


def test_expand_context_rejects_a_non_integer_section_index():
    result = _run("expand_context", {"source_file": "d.pdf", "section_index": "middle"})
    assert result.payload["error"] == "section_index must be an integer"


def test_expand_context_floors_a_negative_window(monkeypatch):
    """A negative window would make the backend's range empty or inverted."""
    seen: dict = {}

    async def fake_expand(source_file, section_index, window=1, collection=None):
        seen.update(source_file=source_file, section_index=section_index, window=window)
        return _results(1)

    monkeypatch.setattr(rag_tool, "expand_context", fake_expand)
    result = _run("expand_context", {"source_file": "d.pdf", "section_index": 3, "window": -5})

    assert seen["window"] == 0
    assert result.payload["window"] == 0
    assert result.step_output == {"chunks": 1}


def test_expand_context_falls_back_to_the_configured_window(monkeypatch):
    seen: dict = {}

    async def fake_expand(source_file, section_index, window=1, collection=None):
        seen["window"] = window
        return []

    monkeypatch.setattr(rag_tool, "expand_context", fake_expand)
    _run("expand_context", {"source_file": "d.pdf", "section_index": 1}, ToolContext(expand_window=4))
    assert seen["window"] == 4

    _run(
        "expand_context",
        {"source_file": "d.pdf", "section_index": 1, "window": "wide"},
        ToolContext(expand_window=4),
    )
    assert seen["window"] == 4, "an unparseable window should fall back, not raise"


# --------------------------------------------------------------------------- #
# fetch_document
# --------------------------------------------------------------------------- #
def test_fetch_document_requires_a_source_file():
    result = _run("fetch_document", {})
    assert result.payload["error"] == "source_file is required"


def test_fetch_document_points_a_bad_id_at_list_documents(monkeypatch):
    """The model guesses document names; the error has to say how to get a real one."""

    async def fake_fetch(source_file, collection=None, max_chunks=200):
        return []

    monkeypatch.setattr(rag_tool, "fetch_document", fake_fetch)
    result = _run("fetch_document", {"source_file": "Kage 2018"})

    assert "list_documents" in result.payload["error"]
    assert result.results == []


@pytest.mark.parametrize("returned,truncated", [(9, False), (10, True)])
def test_fetch_document_flags_truncation_at_the_cap(monkeypatch, returned, truncated):
    """`truncated` tells the model its overview may be partial — off by one here
    means a silently incomplete summary."""
    seen: dict = {}

    async def fake_fetch(source_file, collection=None, max_chunks=200):
        seen["max_chunks"] = max_chunks
        return _results(returned)

    monkeypatch.setattr(rag_tool, "fetch_document", fake_fetch)
    result = _run("fetch_document", {"source_file": "d.pdf"}, ToolContext(fetch_max_chunks=10))

    assert seen["max_chunks"] == 10, "the cap must reach the backend, not just the payload"
    assert result.payload["chunks"] == returned
    assert result.payload["truncated"] is truncated


# --------------------------------------------------------------------------- #
# verify_claim
# --------------------------------------------------------------------------- #
def test_verify_claim_requires_a_claim():
    result = _run("verify_claim", {"claim": ""})
    assert result.payload["error"] == "claim is required"


@pytest.mark.parametrize("supported", [True, False])
def test_verify_claim_passes_the_support_signal_through(monkeypatch, supported):
    """An unsupported claim is the whole point of the tool — it must not be
    flattened into a plain hit list."""

    async def fake_verify(claim, filters=None, collection=None):
        return _results(2), supported

    monkeypatch.setattr(rag_tool, "verify_claim", fake_verify)
    result = _run("verify_claim", {"claim": "the study had 400 participants"})

    assert result.payload["supported"] is supported
    assert result.step_output == {"supported": supported, "hits": 2}
    assert len(result.results) == 2, "evidence stays citable either way"


# --------------------------------------------------------------------------- #
# list_documents
# --------------------------------------------------------------------------- #
def test_list_documents_is_navigational_and_produces_no_citations(monkeypatch):
    """It answers "what is in the KB?" — those rows must never enter the citation
    panel as if they were retrieved evidence."""

    async def fake_list(collection=None):
        return [{"source_file": "a.pdf", "chunks": 3}, {"source_file": "b.pdf", "chunks": 1}]

    monkeypatch.setattr(rag_tool, "list_documents", fake_list)
    result = _run("list_documents", {})

    assert result.payload["count"] == 2
    assert result.results == []
    assert result.step_output == {"documents": 2}
