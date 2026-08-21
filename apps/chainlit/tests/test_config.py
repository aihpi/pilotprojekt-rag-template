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
