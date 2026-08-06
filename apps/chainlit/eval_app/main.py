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
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db(DB_PATH)
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


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    judge_model: str
    config_signature: str | None = None
    comment: str | None = None
    step_id: str | None = None
    thread_id: str | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
async def stats() -> dict[str, list]:
    """Everything the dashboard draws, in one request.

    One endpoint rather than one per widget: the payload is a handful of rows per
    configuration, so splitting it would only buy the page more round-trips to
    coordinate.
    """
    return {
        "configs": storage.stats_by_config(DB_PATH),
        "failures": storage.failure_categories(DB_PATH),
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
    )
    return scores
