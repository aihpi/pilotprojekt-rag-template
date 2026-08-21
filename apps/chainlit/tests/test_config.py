"""Config schema + loader tests: defaults, env overrides, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import load_config
from config.schema import RagConfig

APP = Path(__file__).resolve().parent.parent
DEFAULT = APP / "config" / "default.yaml"
MINIMAL = APP / "examples" / "minimal" / "rag.config.yaml"
PAPERS = APP / "examples" / "papers" / "rag.config.yaml"
MULTI = APP / "examples" / "papers" / "rag.config.multi-source.yaml"
SHIPPED = sorted((APP / "examples").glob("*/*.yaml"))


def test_default_config_is_neutral():
    cfg = load_config(DEFAULT)
    assert cfg.vector_store.collection == "documents"
    assert cfg.language == "en"
    assert cfg.citation.token_word == "Source"
    assert cfg.profiles == [] and cfg.profiles_path is None
    assert cfg.prompt.starter_questions == []
    # neutral system prompt exists and carries no domain terms
    prompt = cfg.resolve_path(cfg.prompt.system_prompt_path).read_text(encoding="utf-8")
    assert not any(w in prompt for w in ("Grundschutz", "Baustein", "Quelle"))


def test_papers_example_enables_all_features():
    """The shipped reference instance must keep demonstrating every feature —
    it is what a fresh clone runs."""
    cfg = load_config(PAPERS)
    assert cfg.vector_store.collection == "papers"
    # agentic tools
    assert cfg.tools.enabled == [
        "search", "list_documents", "fetch_document", "expand_context", "verify_claim"
    ]
    # hybrid retrieval — the example is where it is demonstrated, so it stays on
    # here even though the schema default is off
    assert cfg.retrieval.hybrid is True
    assert cfg.retrieval.fusion == "rrf"
    assert cfg.retrieval.prefetch_limit >= cfg.retrieval.max_top_k
    # answer-quality scoring — the reference instance is where the badge is
    # demonstrated, so it stays on here even though the schema default is off
    assert cfg.evaluation.enabled is True
    assert cfg.evaluation.judge_model, "a pinned judge, or models grade their own work"
    # figure handling incl. inline placement
    assert cfg.images.mode == "describe"
    assert cfg.images.inline_figures is True
    # semantic chunking (set per source, overriding the global default)
    source = cfg.data_sources[0]
    assert (source.chunking or cfg.chunking).strategy == "semantic"
    # the shipped corpus is reachable and holds the example PDFs
    documents = cfg.resolve_path(source.path)
    assert documents.is_dir()
    assert len(list(documents.glob("*.pdf"))) >= 3


def test_minimal_config_uses_schema_defaults():
    cfg = load_config(MINIMAL)
    assert cfg.vector_store.collection == "my_docs"
    assert cfg.chunking.strategy == "fixed_size"  # default
    assert cfg.sources.served_extensions == [".pdf", ".txt", ".md"]  # default
    assert [s.format for s in cfg.data_sources] == ["pdf"]


def test_env_overrides_win(monkeypatch):
    monkeypatch.setenv("QDRANT_COLLECTION", "override")
    monkeypatch.setenv("CHAT_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("TOP_K", "9")
    monkeypatch.setenv("STARTER_QUESTIONS", "A||B||C")
    cfg = load_config(DEFAULT)
    assert cfg.vector_store.collection == "override"
    assert cfg.models.chat_model == "openai/gpt-oss-120b"
    assert cfg.retrieval.top_k == 9  # coerced to int
    assert cfg.prompt.starter_questions == ["A", "B", "C"]


def test_invalid_overlap_raises():
    with pytest.raises(Exception):
        RagConfig.model_validate({"chunking": {"max_chars": 100, "overlap": 500}})


def test_missing_config_file_raises_clear_error():
    with pytest.raises(FileNotFoundError) as exc:
        load_config(APP / "config" / "does-not-exist.yaml")
    assert "RAG_CONFIG" in str(exc.value)


def test_json_csv_require_field_mapping():
    with pytest.raises(Exception):
        RagConfig.model_validate(
            {"data_sources": [{"name": "x", "path": "d.csv", "format": "csv"}]}
        )


def test_settings_shim_exports_expected_constants():
    import importlib
    import sys

    # Reload only `settings` (not the config package) so we don't create
    # duplicate schema classes that would break isinstance checks elsewhere.
    import config.loader as loader

    loader.get_config.cache_clear()
    sys.modules.pop("settings", None)
    settings = importlib.import_module("settings")
    # Neutral default: constants resolve and have the right types.
    assert settings.CHAT_MODEL == "gpt-oss-120b"
    assert settings.QDRANT_COLLECTION == "documents"
    assert settings.TOP_K == 5
    assert settings.MAX_TOP_K == settings.TOP_K
    assert settings.CHUNK_MAX_CHARS > 0
    assert settings.SYSTEM_PROMPT_PATH.is_file()
    assert isinstance(settings.starter_questions(), list)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.parent.name + "/" + p.name)
def test_every_shipped_example_validates(path):
    """An example that no longer loads is worse than no example, and nothing else
    exercises these files."""
    load_config(path)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.parent.name + "/" + p.name)
def test_profile_filters_are_allowed_by_filterable_fields(path):
    """A filter on a field missing from `filterable_fields` is dropped silently, so a
    role would appear to do nothing. Measured: retrieve() builds no condition for it.
    That failure is invisible at load time, which is why it is asserted here."""
    cfg = load_config(path)
    allowed = set(cfg.retrieval.filterable_fields)
    for profile in cfg.profiles:
        missing = set(profile.retrieval_filters) - allowed
        assert not missing, (
            f"profile {profile.id!r} filters on {sorted(missing)}, which is not in "
            f"retrieval.filterable_fields — the filter would be ignored at query time"
        )


def test_multi_source_example_demonstrates_parts_of_a_corpus():
    """The point of that file: several sources, each labelled, each reachable through
    its own role, over the same PDFs the annotated example uses."""
    cfg = load_config(MULTI)

    assert cfg.vector_store.collection != load_config(PAPERS).vector_store.collection, (
        "a second instance must not share the annotated example's collection"
    )
    assert cfg.retrieval.hybrid is True
    labels = [tuple(s.extra_metadata.items()) for s in cfg.data_sources]
    assert len(cfg.data_sources) >= 3
    assert all(labels), "every source carries a label, or filtering has nothing to bite"
    assert len(set(labels)) == len(labels), "labels must distinguish the parts"
    # every label is reachable through some role, and one role searches everything
    filtered = {v for p in cfg.profiles for v in p.retrieval_filters.values()}
    assert filtered == {v for s in cfg.data_sources for v in s.extra_metadata.values()}
    assert any(not p.retrieval_filters for p in cfg.profiles), "one role sees all parts"
    # and the files actually exist, split disjointly
    seen: set[str] = set()
    for src in cfg.data_sources:
        files = {p.name for p in cfg.resolve_path(src.path).glob(src.glob)}
        assert files, f"source {src.name} matches no files"
        assert not (files & seen), f"source {src.name} overlaps another part"
        seen |= files


def test_a_profile_can_filter_on_several_values_and_several_fields():
    """Documented in adding-data.md: a list is OR within one field, several keys are
    AND across fields. Both go through retrieve()'s filter builder, so this pins the
    shapes the docs promise rather than the YAML that describes them."""
    import asyncio

    import rag_tool
    from qdrant_client.models import MatchAny, MatchValue

    class Recording:
        def __init__(self):
            self.filters = []

        def get_collections(self):
            return type("R", (), {"collections": [type("C", (), {"name": "kb"})]})()

        def get_collection(self, name):
            params = type("P", (), {"sparse_vectors": None})()
            return type("I", (), {"config": type("C", (), {"params": params})()})()

        def query_points(self, **kw):
            self.filters.append(kw.get("query_filter"))
            return type("R", (), {"points": []})()

    async def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    original_client, original_embed = rag_tool._get_client, rag_tool.embed
    cfg = rag_tool.get_config()
    original_allowed = list(cfg.retrieval.filterable_fields)
    try:
        cfg.retrieval.filterable_fields = ["zeitraum", "kategorie"]
        rag_tool.embed = fake_embed

        def conditions(filters):
            rec = Recording()
            rag_tool._get_client = lambda: rec
            asyncio.run(rag_tool.retrieve("q", top_k=3, filters=filters, collection="kb"))
            return rec.filters[0].must or []

        several_values = conditions({"zeitraum": ["bis_2019", "2020_2023"]})
        assert len(several_values) == 1
        assert isinstance(several_values[0].match, MatchAny)
        assert several_values[0].match.any == ["bis_2019", "2020_2023"]

        several_fields = conditions({"zeitraum": "bis_2019", "kategorie": "handbuch"})
        assert len(several_fields) == 2, "several keys must AND, not overwrite"
        assert all(isinstance(c.match, MatchValue) for c in several_fields)
        assert {c.key for c in several_fields} == {"zeitraum", "kategorie"}

        assert conditions({"autor": "kage"}) == [], (
            "a field outside filterable_fields is dropped — the silent failure the "
            "docs warn about"
        )
    finally:
        cfg.retrieval.filterable_fields = original_allowed
        rag_tool._get_client, rag_tool.embed = original_client, original_embed


def test_and_on_one_field_cannot_match_and_or_can():
    """Documented in adding-data.md: OR widens, AND narrows, and AND across one field
    is always empty because a chunk holds a single value per field. Asserted against
    Qdrant's own filter semantics with an in-memory collection, so it stays true if the
    conditions we build ever change shape."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance, FieldCondition, Filter, MatchAny, MatchValue, PointStruct, VectorParams,
    )

    client = QdrantClient(":memory:")
    client.create_collection("t", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert("t", points=[
        PointStruct(id=1, vector=[1.0, 0.0], payload={"zeitraum": "bis_2019", "art": "paper"}),
        PointStruct(id=2, vector=[0.0, 1.0], payload={"zeitraum": "2020_2023", "art": "paper"}),
        PointStruct(id=3, vector=[1.0, 1.0], payload={"zeitraum": "bis_2019", "art": "flyer"}),
    ])
    count = lambda *c: client.count("t", count_filter=Filter(must=list(c))).count

    one = FieldCondition(key="zeitraum", match=MatchValue(value="bis_2019"))
    other = FieldCondition(key="zeitraum", match=MatchValue(value="2020_2023"))

    assert count(one) == 2
    # OR: the union, wider than either value alone
    assert count(FieldCondition(key="zeitraum", match=MatchAny(any=["bis_2019", "2020_2023"]))) == 3
    # AND across different fields: the intersection, narrower
    assert count(one, FieldCondition(key="art", match=MatchValue(value="paper"))) == 1
    # AND on the same field: nothing can hold two values, so it is always empty
    assert count(one, other) == 0, (
        "if this ever passes, the docs' reason for the list form is wrong"
    )
