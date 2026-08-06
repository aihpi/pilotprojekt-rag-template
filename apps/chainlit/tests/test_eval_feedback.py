"""A failure category the dashboard cannot count is worse than no category.

The classifier reads free text written by a user in any language and has to land
on one of four fixed tokens. Two things go wrong in practice: the model wraps the
answer in a sentence, and the model hedges between two categories. The first must
still be accepted, the second must be thrown away — a hedge stored as a category
becomes a bar in the dashboard that nobody can act on, and it splits counts that
belonged together.

The endpoint tests pin who gets classified: only thumbs-down, and only with a
comment. Running a thumbs-up through a failure taxonomy would invent a failure the
user never reported.

No litellm call happens here; classify() is replaced wholesale.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from eval_app import feedback, main, storage

SIG = "gpt-oss-120b|octen-embedding-8b|semantic|3000|papers"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "eval.sqlite3")
    with TestClient(main.app) as c:
        yield c


def _rows(db):
    with storage.connect(db) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM feedback")]


def _reply(monkeypatch, text):
    """Make litellm.acompletion answer with `text`, without importing litellm."""

    class _Message:
        content = text

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    async def fake_acompletion(**kwargs):
        return _Response()

    import sys
    import types

    stub = types.ModuleType("litellm")
    stub.acompletion = fake_acompletion
    monkeypatch.setitem(sys.modules, "litellm", stub)


def _classify(comment):
    return asyncio.run(feedback.classify(comment, judge_model="gpt-oss-120b"))


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("hallucination", "hallucination"),
        ("incomplete", "incomplete"),
        ("Hallucination.", "hallucination"),  # punctuation and case
        ("Category: wrong_document", "wrong_document"),  # wrapped in a sentence
        ("  irrelevant\n", "irrelevant"),  # whitespace
        ("groundedness", None),  # not one of ours
        ("", None),  # model said nothing
        ("not hallucination, more incomplete", None),  # hedged between two
    ],
)
def test_only_an_unambiguous_category_survives(monkeypatch, reply, expected):
    _reply(monkeypatch, reply)
    assert _classify("Die Antwort stimmt nicht mit dem PDF überein") == expected


def test_an_empty_comment_is_not_sent_to_the_model(monkeypatch):
    def explode(**kwargs):
        raise AssertionError("classify called the model for an empty comment")

    import sys
    import types

    stub = types.ModuleType("litellm")
    stub.acompletion = explode
    monkeypatch.setitem(sys.modules, "litellm", stub)

    assert _classify("   ") is None


def test_a_failing_judge_yields_no_category(monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("gateway 503")

    import sys
    import types

    stub = types.ModuleType("litellm")
    stub.acompletion = fake_acompletion
    monkeypatch.setitem(sys.modules, "litellm", stub)

    assert _classify("war falsch") is None


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


def _post(client, **kw):
    body = {"rating": "down", "judge_model": "gpt-oss-120b", "config_signature": SIG, **kw}
    return client.post("/api/feedback", json=body)


def _fake_classify(monkeypatch, result="hallucination"):
    calls = []

    async def fake(comment, **kwargs):
        calls.append(comment)
        return result

    monkeypatch.setattr(main.feedback_mod, "classify", fake)
    return calls


def test_a_thumbs_down_with_a_comment_is_classified_and_stored(client, monkeypatch):
    calls = _fake_classify(monkeypatch)

    response = _post(client, comment="steht so nicht im PDF", step_id="s-1", thread_id="t-1")

    assert response.json() == {"failure_category": "hallucination"}
    assert calls == ["steht so nicht im PDF"]
    (row,) = _rows(main.DB_PATH)
    assert row["rating"] == "down"
    assert row["failure_category"] == "hallucination"
    assert row["failure_reason"] == "steht so nicht im PDF", "the raw comment is kept too"
    assert (row["step_id"], row["thread_id"]) == ("s-1", "t-1")


def test_a_thumbs_up_is_never_classified(client, monkeypatch):
    calls = _fake_classify(monkeypatch)

    response = _post(client, rating="up", comment="sehr gut")

    assert response.json() == {"failure_category": None}
    assert calls == [], "praise must not be run through a failure taxonomy"
    (row,) = _rows(main.DB_PATH)
    assert row["rating"] == "up" and row["failure_category"] is None


def test_a_thumbs_down_without_a_comment_is_still_recorded(client, monkeypatch):
    calls = _fake_classify(monkeypatch)

    _post(client)

    assert calls == [], "nothing to classify"
    (row,) = _rows(main.DB_PATH)
    assert row["rating"] == "down"
    assert row["failure_category"] is None
    assert row["failure_reason"] is None


def test_an_unusable_category_stores_the_comment_but_no_category(client, monkeypatch):
    _fake_classify(monkeypatch, result=None)

    response = _post(client, comment="war irgendwie daneben")

    assert response.json() == {"failure_category": None}
    (row,) = _rows(main.DB_PATH)
    assert row["failure_reason"] == "war irgendwie daneben", (
        "the comment survives so a human can still read it"
    )
    assert row["failure_category"] is None


def test_a_rating_the_schema_does_not_know_is_rejected(client):
    assert _post(client, rating="sideways").status_code == 422
