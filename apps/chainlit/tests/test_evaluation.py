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
        "dense",
    ]


def test_dense_and_hybrid_runs_do_not_pool_under_one_signature():
    """The same corpus searched dense and searched hybrid is a different retrieval
    path, and the scores are not even on the same scale — cosine versus a fused
    rank. Averaging them together would be meaningless."""
    dense = RagConfig()
    hybrid = RagConfig(retrieval={"hybrid": True})
    assert evaluation.config_signature(dense) != evaluation.config_signature(hybrid)


def test_settings_that_only_apply_to_hybrid_do_not_split_dense_runs():
    """fusion and prefetch_limit are inert when hybrid is off. Recording them
    unconditionally split two behaviourally identical dense runs apart."""
    a = RagConfig(retrieval={"fusion": "rrf", "prefetch_limit": 30})
    b = RagConfig(retrieval={"fusion": "dbsf", "prefetch_limit": 60})
    assert evaluation.config_signature(a) == evaluation.config_signature(b)


def test_the_signature_follows_the_fusion_strategy():
    """RRF and DBSF weight the two legs differently, so they are separate
    configurations to compare, not one to pool."""
    rrf = RagConfig(retrieval={"hybrid": True, "fusion": "rrf"})
    dbsf = RagConfig(retrieval={"hybrid": True, "fusion": "dbsf"})
    assert evaluation.config_signature(rrf) != evaluation.config_signature(dbsf)


def test_the_signature_follows_the_candidate_pool_when_hybrid_is_on():
    """prefetch_limit decides which candidates fusion ever sees, so a wider pool
    can surface a chunk neither leg ranked in its own top-k — a different answer,
    not a comparable run."""
    a = RagConfig(retrieval={"hybrid": True, "prefetch_limit": 30})
    b = RagConfig(retrieval={"hybrid": True, "prefetch_limit": 60})
    assert evaluation.config_signature(a) != evaluation.config_signature(b)


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


def _source(**chunking):
    return {
        "name": "papers",
        "path": "data/documents",
        "format": "pdf",
        "chunking": chunking or None,
    }


def test_the_signature_reports_the_chunking_the_corpus_was_ingested_with():
    """A source overriding the global chunking is the shipped example, not an edge.

    ``examples/papers`` has no top-level ``chunking:`` block at all — it sets
    ``semantic`` inside its data source — so reading the global one reported the
    schema default ``fixed_size``, describing a corpus that was never built.
    """
    cfg = RagConfig(data_sources=[_source(strategy="semantic", max_chars=1500)])
    assert cfg.chunking.strategy == "fixed_size", "the global one is still the default"

    # Indexed, not unpacked: the field list grows (hybrid/fusion landed after this
    # test was written) and a positional unpack fails on every addition.
    strategy, max_chars = evaluation.config_signature(cfg).split("|")[2:4]
    assert (strategy, max_chars) == ("semantic", "1500")


def test_two_sources_keep_each_strategy_paired_with_its_own_size():
    """Deduplicating strategies and sizes independently lost which size went with
    which strategy, so semantic/1500 + heading/3000 and semantic/3000 +
    heading/1500 — two genuinely different corpora — pooled under one signature."""
    def _two(first: int, second: int):
        return RagConfig(
            data_sources=[
                {**_source(strategy="semantic", max_chars=first), "name": "a"},
                {**_source(strategy="heading", max_chars=second), "name": "b"},
            ]
        )

    a, b = _two(1500, 3000), _two(3000, 1500)
    assert evaluation.config_signature(a) != evaluation.config_signature(b)
    # And the two fields stay positionally aligned, so the pairing is readable.
    assert evaluation.effective_chunking(a) == ("heading+semantic", "3000+1500")
    assert evaluation.effective_chunking(b) == ("heading+semantic", "1500+3000")


def test_a_source_without_an_override_still_reports_the_global_chunking():
    cfg = RagConfig(chunking={"strategy": "heading"}, data_sources=[_source()])
    assert evaluation.effective_chunking(cfg)[0] == "heading"


def test_sources_that_disagree_are_reported_as_disagreeing():
    # Several sources can feed one collection. Picking one of them would file every
    # score under a chunking half the corpus never saw.
    cfg = RagConfig(
        data_sources=[
            _source(strategy="semantic", max_chars=1500),
            {**_source(strategy="heading", max_chars=3000), "name": "notes"},
        ]
    )
    # Positionally aligned: heading is the 3000 one, semantic the 1500 one. This
    # previously read ("heading+semantic", "1500+3000") — each field sorted on its
    # own, which silently swapped the sizes onto the wrong strategies.
    assert evaluation.effective_chunking(cfg) == ("heading+semantic", "3000+1500")


def test_the_signature_names_the_model_that_answered_not_the_configured_one():
    """The settings panel lets a user switch models, and that choice is persisted.

    Without this the score is filed under ``models.chat_model``, so a Gemma answer
    lands in the gpt-oss-120b row — wrong in the one field the dashboard groups by.
    """
    cfg = RagConfig(models={"chat_model": "gpt-oss-120b"})
    assert evaluation.config_signature(cfg, "gemma-4-31b").startswith("gemma-4-31b|")
    assert evaluation.config_signature(cfg, None).startswith("gpt-oss-120b|"), (
        "no session model means the configured one, not an empty field"
    )


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


def test_the_session_model_reaches_both_the_signature_and_the_judge(monkeypatch):
    # `judge_model: null` is documented as "the chat model", so it has to mean the
    # one that answered — otherwise switching models silently changes who judges.
    calls = _install_client(monkeypatch)
    cfg = _enabled()
    _post(cfg, chat_model="gemma-4-31b")

    body = calls[0][1]
    assert body["judge_model"] == "gemma-4-31b"
    assert body["config_signature"].startswith("gemma-4-31b|")


def test_a_trailing_slash_on_the_service_url_does_not_double_up(monkeypatch):
    calls = _install_client(monkeypatch)
    _post(_enabled(service_url="http://localhost:8001/"))
    assert calls[0][0] == "http://localhost:8001/api/score"


def test_a_config_with_one_metric_only_asks_for_that_metric(monkeypatch):
    calls = _install_client(monkeypatch)
    _post(_enabled(metrics=["faithfulness"]))
    assert calls[0][1]["metrics"] == ["faithfulness"]


# --------------------------------------------------------------------------- #
# Feedback posting
# --------------------------------------------------------------------------- #


def _feedback(cfg, **kw):
    return asyncio.run(evaluation.post_feedback(rating="down", cfg=cfg, **kw))


def test_a_disabled_config_posts_no_feedback_either(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    assert _feedback(RagConfig()) is None


def test_feedback_carries_the_signature_and_the_raw_comment(monkeypatch):
    calls = _install_client(monkeypatch)
    cfg = _enabled()

    _feedback(cfg, step_id="s-1", thread_id="t-1", comment="steht nicht im PDF")

    url, body = calls[0]
    assert url == "http://eval:8001/api/feedback"
    assert body["rating"] == "down"
    assert body["comment"] == "steht nicht im PDF"
    assert body["config_signature"] == evaluation.config_signature(cfg)
    assert (body["step_id"], body["thread_id"]) == ("s-1", "t-1")


def test_an_unreachable_service_does_not_break_a_thumbs_click(monkeypatch):
    _install_client(monkeypatch, raises=httpx.ConnectError("connection refused"))
    assert _feedback(_enabled(), comment="x") is None


# --------------------------------------------------------------------------- #
# Trend arrow on the badge
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mean, last, answers, expected",
    [
        (0.60, 0.90, 3, 1),  # last answer clearly better than the conversation
        (0.90, 0.60, 3, -1),  # clearly worse
        (0.80, 0.80, 3, 0),  # unchanged
        (0.80, 0.805, 3, 0),  # inside the dead band: judge jitter, not a trend
        (0.80, 0.795, 3, 0),
        # With one answer the last value IS the mean, so an arrow would be noise
        # presented as signal.
        (0.67, 0.67, 1, 0),
        (0.10, 0.90, 1, 0),
        # A metric that never scored must not produce a direction.
        (None, 0.9, 5, 0),
        (0.9, None, 5, 0),
        (None, None, 5, 0),
    ],
)
def test_the_trend_is_a_sign_and_needs_two_answers(mean, last, answers, expected):
    assert evaluation.trend_sign(mean, last, answers) == expected


# --------------------------------------------------------------------------- #
# Star ratings and gold marking (the two action-backed posters)
# --------------------------------------------------------------------------- #


def test_gold_posts_the_turns_and_reports_the_service_reply(monkeypatch):
    calls = _install_client(monkeypatch, payload={"status": "ok", "gold_id": "g-1"})
    turns = [{"user": "Frage?", "assistant": "Antwort. Quelle 1"}]
    reply = asyncio.run(evaluation.post_gold(
        turns=turns, message_id="m-1", cfg=_enabled(),
    ))
    assert reply == {"status": "ok", "gold_id": "g-1"}
    url, body = calls[0]
    assert url.endswith("/api/gold") and body["turns"] == turns


def test_gold_returns_none_when_the_service_is_down(monkeypatch):
    # The caller tells the user; a swallowed gold marking would betray a click.
    _install_client(monkeypatch, raises=ConnectionError("down"))
    reply = asyncio.run(evaluation.post_gold(
        turns=[{"user": "q", "assistant": "a"}], cfg=_enabled(),
    ))
    assert reply is None


def test_gold_with_no_turns_posts_nothing(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    assert asyncio.run(evaluation.post_gold(turns=[], cfg=_enabled())) is None


# --------------------------------------------------------------------------- #
# Conversation turns (feeds gold marking and the benchmark replays)
# --------------------------------------------------------------------------- #

HISTORY = [
    {"role": "system", "content": "Du bist ein Assistent."},
    {"role": "user", "content": "Frage eins?"},
    {"role": "tool", "content": '{"context": "..."}'},
    {"role": "assistant", "content": "Antwort eins. Quelle 1"},
    {"role": "user", "content": "Frage zwei?"},
    {"role": "assistant", "content": "Antwort zwei. Quelle 2"},
    {"role": "user", "content": "Noch unbeantwortet?"},
]


def test_turns_pair_questions_with_answers_and_drop_the_rest():
    turns = evaluation.conversation_turns(HISTORY)
    assert turns == [
        {"user": "Frage eins?", "assistant": "Antwort eins. Quelle 1"},
        {"user": "Frage zwei?", "assistant": "Antwort zwei. Quelle 2"},
    ], "system/tool messages and the trailing unanswered question must fall out"


def test_turns_truncate_to_the_marked_answer():
    assert len(evaluation.conversation_turns(HISTORY, turn_index=1)) == 1


# --------------------------------------------------------------------------- #
# The gold suggestion (the quest marker's decision rule)
# --------------------------------------------------------------------------- #


def _summary(**kw):
    return {
        "last_faithfulness": 0.95,
        "last_relevance": 0.9,
        "last_message_gold": False,
        **kw,
    }


def test_a_strong_answer_is_suggested():
    assert evaluation.gold_suggested(_summary(), _enabled().evaluation)


def test_scores_below_either_threshold_stay_quiet():
    ev = _enabled().evaluation
    assert not evaluation.gold_suggested(_summary(last_faithfulness=0.8), ev)
    assert not evaluation.gold_suggested(_summary(last_relevance=0.5), ev)


def test_a_missing_metric_is_never_treated_as_strong():
    # A failed judge is not evidence of quality.
    ev = _enabled().evaluation
    assert not evaluation.gold_suggested(_summary(last_relevance=None), ev)


def test_an_already_gold_answer_never_nags_again():
    ev = _enabled().evaluation
    assert not evaluation.gold_suggested(_summary(last_message_gold=True), ev)


def test_a_null_threshold_disables_the_suggestion():
    ev = _enabled(gold_min_faithfulness=None).evaluation
    assert not evaluation.gold_suggested(_summary(), ev)
