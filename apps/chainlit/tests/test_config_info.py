"""The /config-info payload: what the header chip claims must match the config.

Only the pure builder is tested — the route around it is one auth check, and the
route/catch-all ordering is covered by test_route_order.py.
"""

from __future__ import annotations

import json
import os

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
