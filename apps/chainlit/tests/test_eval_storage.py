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
    storage.add_score(db, **{**defaults, **kw})


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
    _score(db, contexts=["[1] erster Chunk", "[2] zweiter Chunk"], faithfulness=0.9,
           judge_model="ministral-3-14b")
    with storage.connect(db) as conn:
        row = conn.execute("SELECT * FROM eval_scores").fetchone()
    assert json.loads(row["contexts"]) == ["[1] erster Chunk", "[2] zweiter Chunk"]
    assert row["faithfulness"] == 0.9
    assert row["relevance"] is None, "an uncomputed metric stays null, not 0.0"
    assert row["judge_model"] == "ministral-3-14b", (
        "who judged must be recorded, or a judge change makes history ambiguous"
    )


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
# Per-conversation summary (the badge above the chatbox)
# --------------------------------------------------------------------------- #


def test_an_unscored_conversation_summarises_to_zero_not_an_error(db):
    # A brand-new chat is the normal starting state, so this must be answerable
    # rather than a missing row the browser has to interpret.
    s = storage.thread_summary(db, "t-unknown")
    assert s["answers"] == 0
    assert s["faithfulness"] is None and s["relevance"] is None
    assert s["last_faithfulness"] is None


def test_the_summary_averages_only_this_conversation(db):
    _score(db, thread_id="t-1", faithfulness=1.0)
    _score(db, thread_id="t-1", faithfulness=0.5)
    _score(db, thread_id="t-2", faithfulness=0.0)

    s = storage.thread_summary(db, "t-1")
    assert s["answers"] == 2
    assert s["faithfulness"] == pytest.approx(0.75), "t-2 must not leak in"


def test_a_failed_metric_is_excluded_from_the_mean_but_counted_as_an_answer(db):
    _score(db, thread_id="t-1", faithfulness=0.8)
    _score(db, thread_id="t-1", faithfulness=None)  # judge failed

    s = storage.thread_summary(db, "t-1")
    assert s["faithfulness"] == pytest.approx(0.8), "a null must not pull the mean down"
    assert s["answers"] == 2, (
        "the count is of answers scored, so the badge cannot overstate its evidence"
    )


def test_the_last_value_is_per_metric_not_per_row(db):
    # The newest row carrying a faithfulness score need not be the newest row
    # carrying a relevance one, which is why they are looked up separately.
    _score(db, thread_id="t-1", faithfulness=0.2, relevance=0.9)
    _score(db, thread_id="t-1", faithfulness=0.4, relevance=None)

    s = storage.thread_summary(db, "t-1")
    assert s["last_faithfulness"] == 0.4
    assert s["last_relevance"] == 0.9, "fall back to the newest row that actually has one"


def test_relevance_absent_throughout_is_reported_as_absent(db):
    # The normal partial case: faithfulness worked, relevance did not.
    _score(db, thread_id="t-1", faithfulness=0.7)

    s = storage.thread_summary(db, "t-1")
    assert s["faithfulness"] == pytest.approx(0.7)
    assert s["relevance"] is None and s["last_relevance"] is None


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


# --------------------------------------------------------------------------- #
# Routing claims to the chunks worth checking them against
# --------------------------------------------------------------------------- #


def test_a_small_context_is_not_routed_at_all():
    """With no more chunks than the budget there is nothing to choose between."""
    routed = metrics.route_contexts(["claim a", "claim b"], ["chunk x", "chunk y"], budget=4)
    assert routed == ["chunk x\nchunk y"] * 2


def test_a_claim_is_routed_to_the_chunk_that_shares_its_words():
    chunks = [
        "fibronektin adhaesionsrate wurde gemessen",
        "kollagen substrat ergebnisse tabelle",
        "laminin oberflaeche beschichtung daten",
        "photonen detektion zaehlrate histogramm",
        "kalibrierung labor aufbau beschreibung",
    ]
    (routed,) = metrics.route_contexts(
        ["Die Adhaesionsrate von Fibronektin wurde gemessen"], chunks, budget=2
    )
    assert "fibronektin" in routed
    assert len(routed.split("\n")) == 2, "never more than the budget"


def test_routing_keeps_retrieval_order():
    # The judge sees a numbered context, so shuffling it would change what "[1]" means.
    chunks = ["alpha unique", "beta words", "gamma terms", "delta items", "alpha again"]
    (routed,) = metrics.route_contexts(["alpha"], chunks, budget=2)
    assert routed.split("\n") == ["alpha unique", "alpha again"]


def test_every_claim_gets_its_own_context():
    chunks = ["fibronektin rate", "photonen zaehlung", "kalibrierung aufbau", "tabelle werte", "laminin daten"]
    routed = metrics.route_contexts(["fibronektin", "photonen"], chunks, budget=1)
    assert "fibronektin" in routed[0] and "photonen" in routed[1]
    assert routed[0] != routed[1], "routing must not collapse to one shared context"


# --------------------------------------------------------------------------- #
# Similarity guard (the full metric needs ragas, which the app venv does not
# carry — its real path was verified against the eval image directly)
# --------------------------------------------------------------------------- #


def test_similarity_without_a_reference_is_absent_not_zero():
    import asyncio

    result = asyncio.run(
        metrics._score_one("similarity", "q", "a", [], llm=None, embeddings=None)
    )
    assert result == {}, "no reference means 'could not measure', never 0.0"


# --------------------------------------------------------------------------- #
# Star ratings
# --------------------------------------------------------------------------- #


def test_a_rating_outside_one_to_five_is_refused(db):
    for stars in (0, 6):
        with pytest.raises(sqlite3.IntegrityError):
            storage.add_rating(db, stars=stars, config_signature=SIG_A)


def test_ratings_average_into_the_config_stats(db):
    _score(db, faithfulness=0.9)
    storage.add_rating(db, stars=5, config_signature=SIG_A)
    storage.add_rating(db, stars=2, config_signature=SIG_A)
    row = _stats(db)[SIG_A]
    assert row["stars"] == pytest.approx(3.5)
    assert row["stars_n"] == 2


# --------------------------------------------------------------------------- #
# Gold conversations
# --------------------------------------------------------------------------- #

TURNS = [
    {"user": "Welche Paper gibt es?", "assistant": "Drei Paper. Quelle 1"},
    {"user": "Fasse das erste zusammen.", "assistant": "Es zeigt X. Quelle 1"},
]


def test_a_gold_conversation_round_trips_with_its_turns(db):
    gold_id = storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")
    (entry,) = storage.list_gold(db)
    assert entry["id"] == gold_id
    assert entry["turns"] == TURNS, "turns come back parsed, not as a JSON string"


def test_marking_the_same_answer_twice_is_one_row_with_one_id(db):
    first = storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")
    second = storage.add_gold(db, turns=TURNS[:1], config_signature=SIG_B, message_id="m-1")
    assert first == second, "the second click must not mint a new reference"
    assert len(storage.list_gold(db)) == 1


def test_a_retired_gold_row_leaves_the_active_set(db):
    storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")
    with storage.connect(db) as conn:
        conn.execute("UPDATE gold_answers SET active = 0")
    assert storage.list_gold(db) == []
    assert len(storage.list_gold(db, active_only=False)) == 1


# --------------------------------------------------------------------------- #
# Replay provenance and benchmark aggregation
# --------------------------------------------------------------------------- #


def test_replay_rows_stay_out_of_the_live_table(db):
    # One click of the play button must not rewrite what real usage looked like.
    _score(db, faithfulness=1.0)
    _score(db, faithfulness=0.0, source="replay", run_label="run-1", similarity=0.9)
    row = _stats(db)[SIG_A]
    assert row["answers"] == 1
    assert row["faithfulness"] == pytest.approx(1.0), "the replay 0.0 must not drag it"


def test_benchmark_stats_group_by_run_and_report_coverage(db):
    storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")
    for turn, sim in ((1, 0.8), (2, 0.6)):
        _score(db, source="replay", run_label="run-1", gold_id="g", gold_turn=turn,
               similarity=sim, faithfulness=1.0)
    _score(db, sig=SIG_B, source="replay", run_label="run-2", similarity=1.0)

    stats = storage.benchmark_stats(db)
    assert stats["gold_turns_total"] == 2, "coverage denominator is active gold turns"
    by_run = {(r["run_label"], r["config_signature"]): r for r in stats["runs"]}
    assert by_run[("run-1", SIG_A)]["n"] == 2
    assert by_run[("run-1", SIG_A)]["similarity"] == pytest.approx(0.7)
    assert by_run[("run-2", SIG_B)]["n"] == 1


# --------------------------------------------------------------------------- #
# Benchmark jobs
# --------------------------------------------------------------------------- #


def test_jobs_are_claimed_oldest_first_and_exactly_once(db):
    first = storage.create_job(db, chat_model="a", run_label="r-a")
    second = storage.create_job(db, chat_model="b", run_label="r-b")

    assert storage.claim_pending_job(db)["id"] == first
    assert storage.claim_pending_job(db)["id"] == second
    assert storage.claim_pending_job(db) is None, "a claimed job is never handed out again"


def test_job_progress_updates_only_what_was_sent(db):
    job_id = storage.create_job(db, chat_model="a", run_label="r")
    storage.update_job(db, job_id, total_turns=4)
    storage.update_job(db, job_id, done_turns=2)
    storage.update_job(db, job_id, status="done")
    (job,) = storage.list_jobs(db)
    assert (job["status"], job["done_turns"], job["total_turns"]) == ("done", 2, 4)


# --------------------------------------------------------------------------- #
# Migration: a database from before these features
# --------------------------------------------------------------------------- #


def test_an_old_database_gains_the_new_columns_and_tables(tmp_path):
    """The upgrade path is a container restart, nothing else.

    Recreate the pre-benchmark schema by hand, then run init_db over it — every
    new column must arrive via ALTER and old rows must land in the live stats.
    """
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE eval_scores (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, message_id TEXT,
                thread_id TEXT, question TEXT NOT NULL, answer TEXT NOT NULL,
                contexts TEXT NOT NULL DEFAULT '[]', config_signature TEXT NOT NULL,
                faithfulness REAL, relevance REAL, detail TEXT)"""
        )
        conn.execute(
            "INSERT INTO eval_scores (id, timestamp, question, answer, config_signature,"
            " faithfulness) VALUES ('x', '2026-01-01', 'q', 'a', ?, 0.5)",
            (SIG_A,),
        )

    storage.init_db(path)

    row = {r["config_signature"]: r for r in storage.stats_by_config(path)}[SIG_A]
    assert row["answers"] == 1, "pre-migration rows default to source='live'"
    with storage.connect(path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(eval_scores)")}
    assert "judge_model" in cols
    storage.add_gold(path, turns=TURNS, config_signature=SIG_A)
    assert storage.claim_pending_job(path) is None, "jobs table exists and is empty"


# --------------------------------------------------------------------------- #
# The gold suggestion's two storage facts
# --------------------------------------------------------------------------- #


def test_the_summary_names_the_newest_scored_answer(db):
    _score(db, thread_id="t-1", message_id="m-old", faithfulness=0.5)
    _score(db, thread_id="t-1", message_id="m-new", faithfulness=1.0)

    s = storage.thread_summary(db, "t-1")
    assert s["last_message_id"] == "m-new"
    assert s["last_message_gold"] is False


def test_the_summary_knows_when_that_answer_is_already_gold(db):
    _score(db, thread_id="t-1", message_id="m-1", faithfulness=1.0)
    storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")

    assert storage.thread_summary(db, "t-1")["last_message_gold"] is True


def test_a_retired_gold_row_lets_the_suggestion_return(db):
    _score(db, thread_id="t-1", message_id="m-1", faithfulness=1.0)
    storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")
    with storage.connect(db) as conn:
        conn.execute("UPDATE gold_answers SET active = 0")

    assert storage.thread_summary(db, "t-1")["last_message_gold"] is False


def test_gold_takes_its_signature_from_the_score_row_when_one_exists(db):
    # The score row recorded the model that actually answered; a caller without a
    # session can only offer the configured default. The recorded truth wins.
    _score(db, sig=SIG_B, message_id="m-1", faithfulness=1.0)
    storage.add_gold(db, turns=TURNS, config_signature=SIG_A, message_id="m-1")

    (entry,) = storage.list_gold(db)
    assert entry["config_signature"] == SIG_B
