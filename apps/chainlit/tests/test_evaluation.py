"""Evaluation must be free when it is off, and silent when it is broken.

Two invariants protect the answer path, and both are the kind that rot quietly.
With ``evaluation.enabled: false`` the app must not so much as construct an HTTP
client — that is the whole promise that keeps this feature from costing anything
for the people who never switch it on. And because the eval service is optional
and separate, *not running* is its normal state: a scoring call that cannot reach
it must degrade to ``None`` rather than raise into the message handler and lose an
answer the user already waited for.

The signature tests pin the grouping key. ``collection`` is part of it because two
configurations differing only by collection would otherwise share a signature and
silently pool scores from different corpora.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import evaluation
from config.schema import RagConfig

# --------------------------------------------------------------------------- #
# Fake HTTP clients (hand-rolled; the suite uses no unittest.mock)
# --------------------------------------------------------------------------- #


class _ExplodingClient:
    """Stand-in for httpx.AsyncClient that fails the test if it is ever built."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("evaluation opened an HTTP client when it should not have")


def _install_client(monkeypatch, *, payload=None, raises=None):
    """Swap in a fake AsyncClient; returns the list its POSTs are recorded into.

    A closure rather than class attributes, so no configuration leaks from one
    test into the next.
    """
    calls: list[tuple[str, dict]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return payload or {}

    class _Fake:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            if raises:
                raise raises
            calls.append((url, json))
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Fake)
    return calls


def _enabled(**evaluation_kw) -> RagConfig:
    return RagConfig(evaluation={"enabled": True, **evaluation_kw})


def _post(cfg, **kw):
    defaults = {
        "question": "Welche Adhäsionsraten wurden gemessen?",
        "answer": "Zwischen 40 und 60 Prozent.",
        "contexts": ["[1] Die Adhäsionsrate lag bei 40-60%."],
        "cfg": cfg,
    }
    return asyncio.run(evaluation.post_score(**{**defaults, **kw}))


# --------------------------------------------------------------------------- #
# Config signature
# --------------------------------------------------------------------------- #


def test_the_signature_names_every_field_that_changes_results():
    cfg = RagConfig()
    signature = evaluation.config_signature(cfg)
    assert signature.split("|") == [
        cfg.models.chat_model,
        cfg.models.embed_model,
        cfg.chunking.strategy,
        str(cfg.chunking.max_chars),
        cfg.vector_store.collection,
    ]


def test_configs_differing_only_by_collection_get_different_signatures():
    a = RagConfig(vector_store={"collection": "papers_semantic"})
    b = RagConfig(vector_store={"collection": "papers_heading"})
    assert evaluation.config_signature(a) != evaluation.config_signature(b), (
        "scores from different corpora must not pool under one signature"
    )


def test_the_signature_follows_the_chunking_strategy():
    a = RagConfig(chunking={"strategy": "semantic"})
    b = RagConfig(chunking={"strategy": "heading"})
    assert evaluation.config_signature(a) != evaluation.config_signature(b)


# --------------------------------------------------------------------------- #
# Zero overhead while disabled
# --------------------------------------------------------------------------- #


def test_a_disabled_config_never_opens_a_connection(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    assert _post(RagConfig()) is None, "disabled evaluation must return no scores"


def test_an_answer_with_no_retrieved_context_is_not_scored(monkeypatch):
    # Faithfulness is unanswerable without chunks, and booking a 0.0 against a
    # correct "not in the documents" answer would drag every aggregate down.
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    assert _post(_enabled(), contexts=[]) is None


def test_an_empty_answer_is_not_scored(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    assert _post(_enabled(), answer="   ") is None


# --------------------------------------------------------------------------- #
# Silent degradation
# --------------------------------------------------------------------------- #


def test_an_unreachable_eval_service_yields_no_scores_and_no_exception(monkeypatch):
    _install_client(monkeypatch, raises=httpx.ConnectError("connection refused"))
    assert _post(_enabled()) is None, (
        "a missing eval service must not raise into the message handler"
    )


# --------------------------------------------------------------------------- #
# What gets posted
# --------------------------------------------------------------------------- #


def test_the_posted_payload_carries_the_signature_and_resolved_judge(monkeypatch):
    calls = _install_client(monkeypatch, payload={"faithfulness": 0.9, "relevance": 0.8})

    cfg = _enabled()
    scores = _post(cfg, thread_id="t-1", message_id="m-1")

    assert scores == {"faithfulness": 0.9, "relevance": 0.8}
    assert len(calls) == 1
    url, body = calls[0]
    assert url == "http://eval:8001/api/score"
    assert body["config_signature"] == evaluation.config_signature(cfg)
    assert body["thread_id"] == "t-1" and body["message_id"] == "m-1"
    assert body["judge_model"] == cfg.models.chat_model, (
        "judge_model: null must resolve to the chat model, not stay null"
    )


def test_an_explicit_judge_model_wins_over_the_chat_model(monkeypatch):
    calls = _install_client(monkeypatch)
    _post(_enabled(judge_model="gemma-4-31b"))
    assert calls[0][1]["judge_model"] == "gemma-4-31b"


def test_a_trailing_slash_on_the_service_url_does_not_double_up(monkeypatch):
    calls = _install_client(monkeypatch)
    _post(_enabled(service_url="http://localhost:8001/"))
    assert calls[0][0] == "http://localhost:8001/api/score"


def test_a_config_with_one_metric_only_asks_for_that_metric(monkeypatch):
    calls = _install_client(monkeypatch)
    _post(_enabled(metrics=["faithfulness"]))
    assert calls[0][1]["metrics"] == ["faithfulness"]


# --------------------------------------------------------------------------- #
# Inline rendering
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "scores, expected",
    [
        ({"faithfulness": 0.923, "relevance": 0.871}, "Faithfulness: 92% · Relevance: 87%"),
        ({"faithfulness": 0.5}, "Faithfulness: 50%"),  # one metric configured
        ({"relevance": 1.0}, "Relevance: 100%"),
        # The reason strings ride along in the same dict and must not be rendered.
        ({"faithfulness": 0.5, "faithfulness_reason": "claim 2 unsupported"}, "Faithfulness: 50%"),
        ({}, ""),  # nothing computed
        (None, ""),  # scoring failed or was skipped
        ({"faithfulness": None}, ""),  # metric returned nothing usable
    ],
)
def test_inline_rendering(scores, expected):
    assert evaluation.format_inline(scores) == expected
