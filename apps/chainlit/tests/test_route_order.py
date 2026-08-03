"""Tests that custom routes are not swallowed by an SPA catch-all.

Chainlit registers ``@router.get("/{full_path:path}")`` to serve its frontend. Because
Starlette matches in list order, any route added after it is unreachable — which silently
broke ``/sources/pdf/*``, ``/sources/figure/*``, ``/sources/citations/*`` and both
``/export/*`` endpoints when FastAPI changed how ``include_router`` stores routes.

These tests build a real FastAPI app rather than a stub route table, so they fail if
FastAPI changes that layout again instead of passing against an assumption.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import app as chainlit_app

CATCH_ALL = "/{full_path:path}"
GUARDED = "/sources/pdf/{file_name:path}"


def _app_with_catch_all_registered_first() -> FastAPI:
    """Reproduce Chainlit's layout: catch-all via include_router, our route added after."""
    fastapi_app = FastAPI()
    spa = APIRouter()

    @spa.get(CATCH_ALL)
    async def serve_spa(full_path: str):  # noqa: ANN202
        return {"served_by": "catch_all"}

    fastapi_app.include_router(spa)

    @fastapi_app.get(GUARDED)
    async def source_pdf(file_name: str):  # noqa: ANN202
        return {"served_by": "source_pdf", "file_name": file_name}

    return fastapi_app


def _catch_all_idx(fastapi_app: FastAPI) -> int | None:
    return next(
        (i for i, r in enumerate(fastapi_app.router.routes) if chainlit_app._contains_catch_all(r)),
        None,
    )


# --------------------------------------------------------------------------- #
# Catch-all detection across FastAPI route layouts
# --------------------------------------------------------------------------- #
def test_catch_all_is_found_however_fastapi_nests_it():
    """The whole bug was this returning None on newer FastAPI."""
    fastapi_app = _app_with_catch_all_registered_first()
    assert _catch_all_idx(fastapi_app) is not None


def test_plain_route_is_not_mistaken_for_a_catch_all():
    fastapi_app = FastAPI()

    @fastapi_app.get(GUARDED)
    async def source_pdf(file_name: str):  # noqa: ANN202
        return {}

    # `/{file_name:path}` must not count — only the SPA's `/{full_path:path}` does.
    assert _catch_all_idx(fastapi_app) is None


# --------------------------------------------------------------------------- #
# Reordering
# --------------------------------------------------------------------------- #
def test_guarded_route_is_moved_ahead_of_the_catch_all():
    fastapi_app = _app_with_catch_all_registered_first()
    before = chainlit_app._find_idx(fastapi_app.router.routes, GUARDED)
    assert before is not None and before > _catch_all_idx(fastapi_app)

    chainlit_app._ensure_route_precedes_catch_all(fastapi_app, GUARDED)

    after = chainlit_app._find_idx(fastapi_app.router.routes, GUARDED)
    assert after is not None and after < _catch_all_idx(fastapi_app)


def test_reorder_is_idempotent():
    fastapi_app = _app_with_catch_all_registered_first()
    chainlit_app._ensure_route_precedes_catch_all(fastapi_app, GUARDED)
    first = chainlit_app._find_idx(fastapi_app.router.routes, GUARDED)
    chainlit_app._ensure_route_precedes_catch_all(fastapi_app, GUARDED)
    assert chainlit_app._find_idx(fastapi_app.router.routes, GUARDED) == first


def test_missing_route_warns_and_does_not_raise(capsys):
    fastapi_app = _app_with_catch_all_registered_first()
    chainlit_app._ensure_route_precedes_catch_all(fastapi_app, "/not/registered")
    assert "route_order" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# End to end: the request actually reaches our handler
# --------------------------------------------------------------------------- #
def test_request_hits_catch_all_before_the_fix():
    """Guards the test itself: if this ever returns source_pdf, the repro is wrong."""
    client = TestClient(_app_with_catch_all_registered_first())
    assert client.get("/sources/pdf/paper.pdf").json()["served_by"] == "catch_all"


def test_request_hits_the_real_handler_after_the_fix():
    fastapi_app = _app_with_catch_all_registered_first()
    chainlit_app._ensure_route_precedes_catch_all(fastapi_app, GUARDED)
    body = TestClient(fastapi_app).get("/sources/pdf/paper.pdf").json()
    assert body["served_by"] == "source_pdf"
    assert body["file_name"] == "paper.pdf"
