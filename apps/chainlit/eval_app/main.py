"""The evaluation service: scores answers, owns the eval database.

Runs as its own container alongside Chainlit. It exists separately for two
reasons: the metric library drags in a dependency chain that would undo the work
of getting the Chainlit image down to 1.94 GB, and judge calls must never sit
between a user and their answer.

The Chainlit app is a client here, not a peer — it posts finished answers and
reads nothing. This service is the only writer of ``eval.sqlite3``.

Run locally:

    uv run uvicorn eval_app.main:app --port 8001
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from eval_app import feedback as feedback_mod
from eval_app import metrics, storage
# Imported by name, not reached through the module: the request model has a field
# called `metrics`, which shadows the module when pydantic resolves annotations.
from eval_app.metrics import SUPPORTED_METRICS, MetricName

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Named-volume path under compose; overridable for local runs. Deliberately not
# under the bind-mounted app directory — a WAL database on a bind mount is how
# this project corrupted its chat history once already.
DB_PATH = Path(os.getenv("EVAL_DB_PATH", "/app/.evaldb/eval.sqlite3"))

STATIC_DIR = Path(__file__).parent / "static"

# Same gateway and credentials the app uses. Passed through by compose exactly as
# they are for the ingest service.
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL") or None
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY") or None


def _require_writable(path) -> None:
    """Fail loudly at startup if the database cannot be written.

    ``init_db`` opening the file proves only that it is *readable*. Every write then
    fails per request with ``attempt to write a readonly database`` and a 500, while
    startup has already logged "database ready" — so the logs say healthy and every
    score, rating and benchmark claim silently dies.

    The way this happens in practice: ``/app/.evaldb`` is a named volume, so the
    image's build-time ``chown eval:eval`` is masked by the mount. A volume first
    written by an older image that ran as root keeps root-owned files, and this
    container (uid 999) cannot write them. Nothing detects that on its own, which is
    why it is checked here rather than left to the first request.
    """
    import sqlite3

    # Rewriting user_version with the value it already has: a real write to the
    # database header that changes nothing. `BEGIN IMMEDIATE` is NOT enough — SQLite
    # defers acquiring the write lock, so it succeeds on a read-only file and the
    # check passes while every actual write still fails. Measured both ways.
    probe = sqlite3.connect(path)
    try:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
        probe.execute(f"PRAGMA user_version = {int(version)}")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"The evaluation database at {path} is not writable ({exc}). Its files are "
            f"probably owned by another user: the volume was first written by an image "
            f"that ran as root, and this service runs as uid 999. Fix the ownership "
            f"(`run`, not `exec` — this service is refusing to start, so there is no "
            f"container to exec into):\n"
            f"    docker compose run --rm -u 0 --entrypoint sh eval "
            f"-c 'chown -R eval:eval /app/.evaldb'\n"
            f"or, if the stored scores are no longer worth keeping (a changed "
            f"config_signature or a re-ingested corpus makes old rows incomparable "
            f"anyway), discard them:\n"
            f"    docker compose down && docker volume rm chainlit_eval_db"
        ) from exc
    finally:
        probe.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db(DB_PATH)
    _require_writable(DB_PATH)
    logger.info("[EVAL] database ready at %s", DB_PATH)
    yield


app = FastAPI(title="RAG Evaluation", lifespan=lifespan)


class ScoreRequest(BaseModel):
    question: str
    answer: str
    contexts: list[str] = Field(default_factory=list)
    # Literal, so an unknown metric is a 422 from pydantic rather than something
    # metrics.py has to hand-check.
    metrics: list[MetricName] = Field(default_factory=lambda: list(SUPPORTED_METRICS))
    judge_model: str
    # relevance compares questions generated from the answer against the real one,
    # so it needs the embedding model as well as the judge.
    embed_model: str
    config_signature: str
    thread_id: str | None = None
    message_id: str | None = None
    # Benchmark replays only: the gold answer to compare against (similarity), and
    # provenance so replay rows never mix into the live statistics.
    reference: str | None = None
    source: Literal["live", "replay"] = "live"
    run_label: str | None = None
    gold_id: str | None = None
    gold_turn: int | None = None


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    judge_model: str
    config_signature: str | None = None
    comment: str | None = None
    step_id: str | None = None
    thread_id: str | None = None


class RatingRequest(BaseModel):
    stars: int = Field(ge=1, le=5)
    message_id: str | None = None
    thread_id: str | None = None
    config_signature: str | None = None


class GoldRequest(BaseModel):
    # One conversation, oldest turn first. Single Q&A = a one-element list.
    turns: list[dict[str, str]] = Field(min_length=1)
    config_signature: str
    thread_id: str | None = None
    message_id: str | None = None


class BenchmarkRequest(BaseModel):
    chat_model: str
    judge_model: str | None = None


class JobUpdate(BaseModel):
    status: Literal["running", "done", "error"] | None = None
    done_turns: int | None = None
    total_turns: int | None = None
    error: str | None = None


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/thread/{thread_id}")
async def thread(thread_id: str) -> dict[str, object]:
    """Running numbers for one conversation, for the badge above the chatbox.

    Always 200, even for a thread nobody has scored: an unscored conversation is
    the normal starting state, and making the caller distinguish 404-because-new
    from 404-because-broken would push that guess into the browser.
    """
    return storage.thread_summary(DB_PATH, thread_id)


@app.get("/api/stats")
async def stats() -> dict[str, object]:
    """Everything the dashboard draws, in one request.

    One endpoint rather than one per widget: the payload is a handful of rows per
    configuration, so splitting it would only buy the page more round-trips to
    coordinate.
    """
    return {
        "configs": storage.stats_by_config(DB_PATH),
        "failures": storage.failure_categories(DB_PATH),
        "gold": storage.list_gold(DB_PATH),
        "benchmark": storage.benchmark_stats(DB_PATH),
        # Recent jobs, so the dashboard can pulse a play button while its run is
        # still going and surface an error where the click happened.
        "jobs": storage.list_jobs(DB_PATH),
    }


@app.post("/api/feedback")
async def post_feedback(request: FeedbackRequest) -> dict[str, str | None]:
    """Record a thumbs click, classifying the comment when there is one.

    Only thumbs-down comments are classified. A thumbs-up with a comment is praise
    or a stray note, and running it through a failure taxonomy would invent a
    failure that the user did not report.
    """
    category = None
    if request.rating == "down" and request.comment:
        category = await feedback_mod.classify(
            request.comment,
            judge_model=request.judge_model,
            base_url=LITELLM_BASE_URL,
            api_key=LITELLM_API_KEY,
        )
    storage.add_feedback(
        DB_PATH,
        rating=request.rating,
        step_id=request.step_id,
        thread_id=request.thread_id,
        config_signature=request.config_signature,
        failure_reason=request.comment,
        failure_category=category,
    )
    return {"failure_category": category}


@app.post("/api/score")
async def post_score(request: ScoreRequest) -> dict[str, object]:
    """Grade one answer, store it, and return the scores for inline display.

    Storing happens even when every metric failed: the row is still evidence that
    a question was asked under this configuration, and the answer count is part of
    what makes the dashboard readable. Scores stay null in that case rather than
    being recorded as zero.
    """
    scores = await metrics.score(
        request.question,
        request.answer,
        request.contexts,
        metrics=request.metrics,
        judge_model=request.judge_model,
        embed_model=request.embed_model,
        base_url=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
        reference=request.reference,
    )
    # Why the judge landed where it did, kept separately from the numbers because it
    # is read whole and never aggregated.
    detail = {
        key: scores[key]
        for key in ("faithfulness_claims", "relevance_declined")
        if key in scores
    }
    storage.add_score(
        DB_PATH,
        question=request.question,
        answer=request.answer,
        contexts=request.contexts,
        config_signature=request.config_signature,
        faithfulness=scores.get("faithfulness"),
        relevance=scores.get("relevance"),
        message_id=request.message_id,
        thread_id=request.thread_id,
        detail=detail or None,
        similarity=scores.get("similarity"),
        source=request.source,
        run_label=request.run_label,
        gold_id=request.gold_id,
        gold_turn=request.gold_turn,
        judge_model=request.judge_model,
    )
    return scores


@app.post("/api/rating")
async def post_rating(request: RatingRequest) -> dict[str, str]:
    """Record a 1-5 star rating for one answer.

    Separate from ``/api/feedback`` because the two carry different identifiers:
    thumbs arrive with Chainlit's ``forId`` (which may name the parent run step),
    stars arrive with the exact assistant message id from an action payload.
    """
    storage.add_rating(
        DB_PATH,
        stars=request.stars,
        message_id=request.message_id,
        thread_id=request.thread_id,
        config_signature=request.config_signature,
    )
    return {"status": "ok"}


@app.post("/api/gold")
async def post_gold(request: GoldRequest) -> dict[str, str | None]:
    """Freeze a conversation as a gold reference for benchmark replays.

    The app sends the turns itself — it holds the session history — so marking
    works even while a judge is still scoring the answer. Marking the same answer
    twice is idempotent (unique ``message_id``).
    """
    gold_id = storage.add_gold(
        DB_PATH,
        turns=request.turns,
        config_signature=request.config_signature,
        thread_id=request.thread_id,
        message_id=request.message_id,
    )
    return {"status": "ok", "gold_id": gold_id}


@app.get("/api/gold")
async def get_gold() -> dict[str, list]:
    """The active gold set, turns included — what a benchmark run replays."""
    return {"gold": storage.list_gold(DB_PATH)}


@app.post("/api/benchmark")
async def post_benchmark(request: BenchmarkRequest) -> dict[str, str]:
    """Queue a benchmark run. The app's poller picks it up.

    A queue rather than a call because the dependency edge is one-way: this
    service cannot answer questions (no retrieval stack in its image) and the app
    takes no inbound calls from it. Refused when the gold set is empty — a run
    over nothing would report itself as a successful benchmark of zero turns.
    """
    if not storage.list_gold(DB_PATH):
        raise HTTPException(status_code=409, detail="no active gold conversations")
    run_label = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} {request.chat_model}"
    job_id = storage.create_job(
        DB_PATH,
        chat_model=request.chat_model,
        judge_model=request.judge_model,
        run_label=run_label,
    )
    return {"status": "queued", "job_id": job_id, "run_label": run_label}


@app.get("/api/benchmark/next")
async def next_benchmark_job() -> dict[str, str | None]:
    """Atomically claim the oldest pending job; ``{}`` when there is none.

    Polled by the app every few seconds while evaluation is enabled.
    """
    return storage.claim_pending_job(DB_PATH) or {}


@app.post("/api/benchmark/{job_id}")
async def update_benchmark_job(job_id: str, request: JobUpdate) -> dict[str, str]:
    storage.update_job(
        DB_PATH,
        job_id,
        status=request.status,
        done_turns=request.done_turns,
        total_turns=request.total_turns,
        error=request.error,
    )
    return {"status": "ok"}
