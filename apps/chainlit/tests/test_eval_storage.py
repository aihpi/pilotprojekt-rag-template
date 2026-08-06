"""The eval store must not lose rows when the two sides are lopsided.

Scores and feedback arrive independently: evaluation can be switched on after
people have already been clicking thumbs, and most answers never get a rating at
all. So a signature can have scores with no feedback, or feedback with no scores.
An aggregate built as a JOIN would quietly drop whichever side is missing and
report a smaller corpus than was actually measured — which is the failure mode
these tests exist to catch, since a dashboard that under-reports looks exactly
like a dashboard that works.

The averaging tests pin the other half of it: a metric that failed is stored as
NULL, never as 0.0, because a judge error is not evidence of a bad answer.

No DeepEval import happens here — storage is stdlib sqlite3, and the one helper
worth testing in metrics.py is pure string handling.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from eval_app import metrics, storage

SIG_A = "gpt-oss-120b|octen-embedding-8b|semantic|3000|papers"
SIG_B = "gpt-oss-120b|octen-embedding-8b|heading|3000|papers_heading"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "eval.sqlite3"
    storage.init_db(path)
    return path


def _score(db, sig=SIG_A, **kw):
    defaults = {
        "question": "Wie hoch war die Adhäsionsrate?",
        "answer": "Zwischen 40 und 60 Prozent.",
        "contexts": ["[1] 40-60%"],
        "config_signature": sig,
    }
    return storage.add_score(db, **{**defaults, **kw})


def _stats(db):
    return {row["config_signature"]: row for row in storage.stats_by_config(db)}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_initialising_twice_is_harmless(tmp_path):
    path = tmp_path / "eval.sqlite3"
    storage.init_db(path)
    storage.init_db(path)  # every open re-runs the schema; must stay idempotent
    assert path.exists()


def test_a_rating_outside_up_or_down_is_refused(db):
    with pytest.raises(sqlite3.IntegrityError):
        storage.add_feedback(db, rating="sideways")


# --------------------------------------------------------------------------- #
# Scores
# --------------------------------------------------------------------------- #


def test_a_score_round_trips_with_its_contexts(db):
    _score(db, contexts=["[1] erster Chunk", "[2] zweiter Chunk"], faithfulness=0.9)
    with storage.connect(db) as conn:
        row = conn.execute("SELECT * FROM eval_scores").fetchone()
    assert json.loads(row["contexts"]) == ["[1] erster Chunk", "[2] zweiter Chunk"]
    assert row["faithfulness"] == 0.9
    assert row["relevance"] is None, "an uncomputed metric stays null, not 0.0"


def test_a_failed_metric_is_null_and_does_not_drag_the_average_down(db):
    _score(db, faithfulness=0.8)
    _score(db, faithfulness=None)  # judge failed on this one
    _score(db, faithfulness=1.0)
    row = _stats(db)[SIG_A]
    assert row["answers"] == 3, "the answer count includes rows whose judge failed"
    assert row["faithfulness"] == pytest.approx(0.9), (
        "AVG must ignore the null, not treat it as zero"
    )


# --------------------------------------------------------------------------- #
# Aggregation across lopsided sides
# --------------------------------------------------------------------------- #


def test_scores_are_reported_for_a_config_nobody_rated(db):
    _score(db, faithfulness=0.7, relevance=0.6)
    row = _stats(db)[SIG_A]
    assert row["answers"] == 1
    assert row["thumbs_up"] == 0 and row["thumbs_down"] == 0


def test_thumbs_are_counted_per_config_and_not_smeared_across_them(db):
    _score(db, sig=SIG_A)
    _score(db, sig=SIG_B)
    storage.add_feedback(db, rating="up", config_signature=SIG_A)
    storage.add_feedback(db, rating="up", config_signature=SIG_A)
    storage.add_feedback(db, rating="down", config_signature=SIG_B)

    stats = _stats(db)
    assert (stats[SIG_A]["thumbs_up"], stats[SIG_A]["thumbs_down"]) == (2, 0)
    assert (stats[SIG_B]["thumbs_up"], stats[SIG_B]["thumbs_down"]) == (0, 1)


def test_many_ratings_on_one_answer_do_not_multiply_the_answer_count(db):
    # The bug a JOIN would introduce: one score row joined to three feedback rows
    # counts as three answers.
    _score(db, faithfulness=0.5)
    for _ in range(3):
        storage.add_feedback(db, rating="up", config_signature=SIG_A)
    row = _stats(db)[SIG_A]
    assert row["answers"] == 1, "feedback rows must not inflate the answer count"
    assert row["thumbs_up"] == 3
    assert row["faithfulness"] == pytest.approx(0.5), (
        "the average must not be re-weighted by how many people clicked"
    )


# --------------------------------------------------------------------------- #
# Failure categories
# --------------------------------------------------------------------------- #


def test_failure_categories_group_per_config(db):
    storage.add_feedback(db, rating="down", config_signature=SIG_A, failure_category="hallucination")
    storage.add_feedback(db, rating="down", config_signature=SIG_A, failure_category="hallucination")
    storage.add_feedback(db, rating="down", config_signature=SIG_A, failure_category="incomplete")
    storage.add_feedback(db, rating="up", config_signature=SIG_A)

    counts = {(r["config_signature"], r["failure_category"]): r["n"] for r in storage.failure_categories(db)}
    assert counts == {(SIG_A, "hallucination"): 2, (SIG_A, "incomplete"): 1}, (
        "thumbs-up rows carry no category and must not appear"
    )


# --------------------------------------------------------------------------- #
# Gateway URL normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "given, expected",
    [
        # The example env file ships the trailing-slash form, so this is the case
        # that actually occurs; appending blindly would give "…//v1" and 404.
        ("https://api.example.de/", "https://api.example.de/v1"),
        ("https://api.example.de", "https://api.example.de/v1"),
        ("https://api.example.de/v1", "https://api.example.de/v1"),  # already there
        ("https://api.example.de/v1/", "https://api.example.de/v1"),  # both at once
        ("http://localhost:4000", "http://localhost:4000/v1"),
    ],
)
def test_the_gateway_url_gets_exactly_one_v1(given, expected):
    assert metrics.openai_base_url(given) == expected
