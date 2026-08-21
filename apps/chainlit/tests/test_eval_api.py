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
    body = client.get("/api/stats").json()
    assert body["configs"] == [] and body["failures"] == []
    assert body["gold"] == [] and body["jobs"] == []
    assert body["benchmark"] == {"gold_turns_total": 0, "runs": []}


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


# --------------------------------------------------------------------------- #
# Gold, ratings and benchmark jobs
# --------------------------------------------------------------------------- #

GOLD_TURNS = [
    {"user": "Welche Paper gibt es?", "assistant": "Drei Paper. Quelle 1"},
    {"user": "Fasse das erste zusammen.", "assistant": "Es zeigt X. Quelle 1"},
]


def _gold_body(**kw):
    return {"turns": GOLD_TURNS, "config_signature": SIG, "message_id": "m-1", **kw}


def test_a_replay_score_carries_its_provenance_and_reference(client, monkeypatch):
    captured = {}

    async def fake(question, answer, contexts, **kwargs):
        captured.update(kwargs)
        return {"faithfulness": 1.0, "similarity": 0.8}

    monkeypatch.setattr(main.metrics, "score", fake)
    client.post("/api/score", json=_body(
        metrics=["faithfulness", "similarity"],
        reference="Die Gold-Antwort.",
        source="replay", run_label="run-1", gold_id="g-1", gold_turn=2,
    ))

    assert captured["reference"] == "Die Gold-Antwort.", "the metric must see the gold answer"
    (row,) = _rows(main.DB_PATH)
    assert (row["source"], row["run_label"], row["gold_id"], row["gold_turn"]) == (
        "replay", "run-1", "g-1", 2
    )
    assert row["similarity"] == 0.8


def test_gold_round_trips_and_marking_twice_is_idempotent(client):
    first = client.post("/api/gold", json=_gold_body()).json()
    second = client.post("/api/gold", json=_gold_body(turns=GOLD_TURNS[:1])).json()
    assert first["gold_id"] == second["gold_id"]

    (entry,) = client.get("/api/gold").json()["gold"]
    assert entry["turns"] == GOLD_TURNS, "the first marking wins; the retry changes nothing"


def test_a_rating_outside_the_scale_is_a_422(client):
    assert client.post("/api/rating", json={"stars": 6}).status_code == 422
    assert client.post("/api/rating", json={"stars": 0}).status_code == 422
    assert client.post("/api/rating", json={"stars": 4, "config_signature": SIG}).status_code == 200


def test_a_benchmark_without_gold_is_refused(client):
    response = client.post("/api/benchmark", json={"chat_model": "gemma-4-31b"})
    assert response.status_code == 409, "a run over nothing would report itself as a benchmark"


def test_the_job_lifecycle_create_claim_progress_done(client):
    client.post("/api/gold", json=_gold_body())
    queued = client.post("/api/benchmark", json={"chat_model": "gemma-4-31b"}).json()
    assert queued["status"] == "queued"

    job = client.get("/api/benchmark/next").json()
    assert job["chat_model"] == "gemma-4-31b"
    assert client.get("/api/benchmark/next").json() == {}, "claimed means gone"

    client.post(f"/api/benchmark/{job['id']}", json={"total_turns": 2})
    client.post(f"/api/benchmark/{job['id']}", json={"done_turns": 2, "status": "done"})

    (listed,) = client.get("/api/stats").json()["jobs"]
    assert (listed["status"], listed["done_turns"], listed["total_turns"]) == ("done", 2, 2)


def test_stats_keep_replay_rows_out_of_configs_but_in_benchmark(client, monkeypatch):
    _fake_score(monkeypatch, {"faithfulness": 1.0, "similarity": 0.9})
    client.post("/api/gold", json=_gold_body())
    client.post("/api/score", json=_body(
        metrics=["faithfulness", "similarity"], reference="ref",
        source="replay", run_label="run-1", gold_id="g-1", gold_turn=1,
    ))

    payload = client.get("/api/stats").json()
    assert payload["configs"] == [], "replay rows must not appear as live usage"
    (run,) = payload["benchmark"]["runs"]
    assert run["run_label"] == "run-1" and run["similarity"] == 0.9
    assert payload["benchmark"]["gold_turns_total"] == 2


# --------------------------------------------------------------------------- #
# The startup writability probe
# --------------------------------------------------------------------------- #
def test_a_writable_database_passes_the_probe(tmp_path):
    from eval_app.main import _require_writable
    from eval_app import storage

    db = tmp_path / "eval.sqlite3"
    storage.init_db(db)
    _require_writable(db)  # must not raise


def test_an_unwritable_database_refuses_startup_with_a_usable_remedy(tmp_path, monkeypatch):
    """A read-only volume used to log "database ready" and then 500 on every write.
    The message is the whole value here, so it is asserted: `exec` cannot work when
    the service is refusing to start, and an unrunnable remedy is worse than none."""
    import sqlite3

    from eval_app.main import _require_writable
    from eval_app import storage

    db = tmp_path / "eval.sqlite3"
    storage.init_db(db)

    real_connect = sqlite3.connect

    class ReadOnly:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a):
            if "user_version =" in sql:
                raise sqlite3.OperationalError("attempt to write a readonly database")
            return self._inner.execute(sql, *a)

        def close(self):
            self._inner.close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: ReadOnly(real_connect(*a, **k)))

    with pytest.raises(RuntimeError) as excinfo:
        _require_writable(db)

    message = str(excinfo.value)
    assert "not writable" in message
    assert "docker compose run --rm" in message, (
        "the remedy must use `run`: `exec` needs a running container, and this check "
        "is what stopped the service from starting"
    )
    assert "docker compose exec" not in message
    assert "chown -R eval:eval" in message and "volume rm" in message, (
        "both ways out belong in the message — repair the ownership, or discard rows "
        "that a changed config_signature made incomparable anyway"
    )
