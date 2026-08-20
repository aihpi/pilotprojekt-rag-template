"""The /config-info payload: what the header chip claims must match the config.

The route itself is one auth check, and catch-all ordering is covered by
test_route_order.py — but its *registration point* is tested here, because it
sits before a ``DATABASE_URL`` early return that would otherwise silence it.
"""

from __future__ import annotations

import inspect
import json
import os
import re

# Importing app registers a Chainlit oauth_callback, which requires an OAuth
# provider to be configured. CI has no .env, so supply placeholders first
# (same preamble as test_route_order.py).
os.environ.setdefault("OAUTH_GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("OAUTH_GITHUB_CLIENT_SECRET", "test-client-secret")

from config.schema import ChunkingConfig, DataSourceConfig, RagConfig  # noqa: E402

import app as chainlit_app  # noqa: E402


def _cfg() -> RagConfig:
    return RagConfig(
        name="unit-test-rag",
        data_sources=[
            DataSourceConfig(name="pdfs", path="docs", format="pdf"),
            DataSourceConfig(
                name="notes",
                path="notes",
                format="md",
                chunking=ChunkingConfig(strategy="heading"),
            ),
        ],
    )


def test_payload_reports_the_effective_chunking_per_source():
    """A per-source override must show as that source's strategy, not the global
    default — the chip exists to answer exactly this kind of question."""
    payload = chainlit_app._config_info_payload(_cfg())

    by_name = {s["name"]: s["chunking"] for s in payload["sources"]}
    assert by_name == {"pdfs": "fixed_size", "notes": "heading"}


def test_route_is_registered_before_the_database_early_return():
    """``on_app_startup`` returns early when DATABASE_URL is unset, and everything
    declared after that point never registers. Auth has an env-var fallback, so a
    no-database instance still has logged-in users — they would get a header chip
    polling a 404 forever. Assert on source order: the failure is *where* the
    route is declared, which no request-level test can see."""
    source = inspect.getsource(chainlit_app.on_app_startup)

    route = source.index('"/config-info"')
    early_return = re.search(r"if not DATABASE_URL:\s*\n\s*return", source)
    assert early_return, "the early return this test guards against is gone; revisit"
    assert route < early_return.start(), (
        "/config-info must be registered before the DATABASE_URL early return"
    )


def test_config_path_cannot_disagree_with_the_loader(monkeypatch):
    """The chip exists to say which file is loaded, so it must report exactly what
    the loader would read — via the loader's own resolver, not a copy of it. Two
    ways a copy drifted: `getenv(name, default)` returns "" for an empty
    RAG_CONFIG where the loader's `or` falls back, and a relative RAG_CONFIG has
    to be joined to BASE_DIR or the chip shows a host-relative path for a file
    the container loaded from /app."""
    from config.loader import CONFIG_PATH_ENV, resolve_config_path

    for value in ("", "examples/papers/rag.config.yaml"):
        monkeypatch.setenv(CONFIG_PATH_ENV, value)
        assert chainlit_app._config_info_payload(_cfg())["config_path"] == str(
            resolve_config_path()
        )

    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    assert chainlit_app._config_info_payload(_cfg())["config_path"] == str(
        resolve_config_path()
    )

    # And it is absolute, which is the half a hand-rolled copy dropped.
    monkeypatch.setenv(CONFIG_PATH_ENV, "examples/papers/rag.config.yaml")
    reported = chainlit_app._config_info_payload(_cfg())["config_path"]
    assert reported.startswith("/") and reported.endswith("examples/papers/rag.config.yaml")


def test_payload_carries_the_retrieval_switches_and_serializes():
    cfg = _cfg()
    cfg.retrieval.hybrid = True
    cfg.retrieval.fusion = "dbsf"
    payload = chainlit_app._config_info_payload(cfg)

    assert payload["name"] == "unit-test-rag"
    assert payload["retrieval"]["hybrid"] is True
    assert payload["retrieval"]["fusion"] == "dbsf"
    assert payload["retrieval"]["prefetch_limit"] == 30
    assert payload["tools"] == ["search"], "empty tools config must fall back to search"
    json.dumps(payload)  # the route returns this verbatim — it has to be JSON-safe


def test_the_copyable_yaml_carries_settings_but_never_secrets():
    """The copy button exists to share configs, which makes it exactly the wrong
    place to learn that the resolved config object holds litellm_api_key and the
    Qdrant api_key. It is built from curated fields, not model_dump(), so nothing
    secret can reach it — this pins that."""
    import yaml as yaml_lib

    cfg = _cfg()
    cfg.models.litellm_api_key = "sk-must-not-appear"
    cfg.vector_store.api_key = "qdrant-must-not-appear"

    text = chainlit_app._config_yaml(cfg)
    assert "must-not-appear" not in text
    assert "api_key" not in text

    parsed = yaml_lib.safe_load(text)
    assert parsed["name"] == "unit-test-rag"
    assert parsed["models"]["chat_model"] == cfg.models.chat_model
    assert parsed["retrieval"]["hybrid"] is False
    assert parsed["tools"]["enabled"] == ["search"]
    # path is omitted, not invented: it cannot transfer, and a placeholder that
    # survives a paste is worse than an absence. It is schema-required, so the
    # gap fails at load naming the field instead of pointing somewhere wrong.
    assert all("path" not in src for src in parsed["data_sources"])
    assert "notes" in [src["name"] for src in parsed["data_sources"]]
    assert "CHANGE-ME" not in text


def test_the_yaml_names_each_source_own_chunking_strategy():
    """The strategy is the field worth comparing between two configs, and it lives
    per source — a top-level chunking block would report the fallback for a corpus
    that never used it (the shipped papers example is exactly that case)."""
    import yaml as yaml_lib

    parsed = yaml_lib.safe_load(chainlit_app._config_yaml(_cfg()))
    by_name = {s["name"]: s["chunking"] for s in parsed["data_sources"]}

    assert by_name["pdfs"]["strategy"] == "fixed_size"      # inherits the global
    assert by_name["notes"]["strategy"] == "heading"        # overrides it

    # Strategy-specific knobs appear only where they mean something.
    assert "semantic_breakpoint_percentile" not in by_name["notes"]
    semantic = yaml_lib.safe_load(
        chainlit_app._config_yaml(
            RagConfig(data_sources=[DataSourceConfig(
                name="s", path="d", format="pdf",
                chunking=ChunkingConfig(strategy="semantic"),
            )])
        )
    )
    assert semantic["data_sources"][0]["chunking"]["semantic_breakpoint_percentile"] == 95.0


def test_the_payload_exposes_the_yaml_for_the_button():
    payload = chainlit_app._config_info_payload(_cfg())
    assert payload["yaml"].startswith("name: unit-test-rag")
