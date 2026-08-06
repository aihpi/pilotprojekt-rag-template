"""The score endpoint must store the row even when every judge call failed.

A judge error is not evidence of a bad answer, so the scores go in as NULL — but
the row still goes in. It is the record that a question was asked under this
configuration, and the answer count is what makes the dashboard's averages
readable ("0.91 over 3 answers" and "0.91 over 300" are not the same claim). Drop
the row on failure and a flaky judge silently rewrites history as though those
questions were never asked.

DeepEval is never imported here: ``metrics.score`` is replaced wholesale, which is
the payoff for keeping the metric library behind one function.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eval_app import main, storage

SIG = "gpt-oss-120b|octen-embedding-8b|semantic|3000|papers"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "eval.sqlite3")
    with TestClient(main.app) as c:
        yield c


def _body(**kw):
    return {
        "question": "Wie hoch war die Adhäsionsrate?",
        "answer": "Zwischen 40 und 60 Prozent.",
        "contexts": ["[1] Die Rate lag bei 40-60%."],
        "metrics": ["faithfulness", "relevance"],
        "judge_model": "gpt-oss-120b",
        "embed_model": "octen-embedding-8b",
        "config_signature": SIG,
        **kw,
    }


def _rows(db):
    with storage.connect(db) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM eval_scores")]


def _fake_score(monkeypatch, result):
    async def fake(question, answer, contexts, **kwargs):
        return result

    monkeypatch.setattr(main.metrics, "score", fake)


def test_health_is_reachable(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_scores_are_returned_and_stored(client, monkeypatch):
    _fake_score(monkeypatch, {"faithfulness": 0.92, "relevance": 0.87})

    response = client.post("/api/score", json=_body(thread_id="t-1", message_id="m-1"))

    assert response.status_code == 200
    assert response.json() == {"faithfulness": 0.92, "relevance": 0.87}
    (row,) = _rows(main.DB_PATH)
    assert (row["faithfulness"], row["relevance"]) == (0.92, 0.87)
    assert (row["thread_id"], row["message_id"]) == ("t-1", "m-1")
    assert row["config_signature"] == SIG


def test_a_total_judge_failure_still_records_the_answer(client, monkeypatch):
    _fake_score(monkeypatch, {})  # every metric raised inside score()

    response = client.post("/api/score", json=_body())

    assert response.status_code == 200
    assert response.json() == {}
    (row,) = _rows(main.DB_PATH)
    assert row["faithfulness"] is None and row["relevance"] is None, (
        "a failed judge stores null, never 0.0"
    )
    assert row["question"], "the row must still say a question was asked"


def test_a_partial_result_stores_the_metric_that_worked(client, monkeypatch):
    _fake_score(monkeypatch, {"relevance": 0.8})

    client.post("/api/score", json=_body())

    (row,) = _rows(main.DB_PATH)
    assert row["relevance"] == 0.8
    assert row["faithfulness"] is None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


def test_the_dashboard_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "RAG Evaluation" in response.text


def test_stats_are_empty_on_a_fresh_database(client):
    # The page keys its "turn evaluation on" panel off an empty configs list, so
    # empty must mean empty rather than an error.
    assert client.get("/api/stats").json() == {"configs": [], "failures": []}


def test_stats_report_scores_and_failures_together(client, monkeypatch):
    _fake_score(monkeypatch, {"faithfulness": 0.9, "relevance": 0.7})
    client.post("/api/score", json=_body())
    storage.add_feedback(
        main.DB_PATH,
        rating="down",
        config_signature=SIG,
        failure_category="hallucination",
    )

    payload = client.get("/api/stats").json()

    (config,) = payload["configs"]
    assert config["config_signature"] == SIG
    assert (config["answers"], config["thumbs_down"]) == (1, 1)
    assert payload["failures"] == [
        {"config_signature": SIG, "failure_category": "hallucination", "n": 1}
    ]


def test_the_reason_strings_are_returned_but_not_stored(client, monkeypatch):
    # The reasons are useful to show a user right now; they are not worth a column
    # until something actually queries them.
    _fake_score(monkeypatch, {"faithfulness": 0.5, "faithfulness_reason": "claim 2 unsupported"})

    response = client.post("/api/score", json=_body())

    assert response.json()["faithfulness_reason"] == "claim 2 unsupported"
    (row,) = _rows(main.DB_PATH)
    assert "faithfulness_reason" not in row
