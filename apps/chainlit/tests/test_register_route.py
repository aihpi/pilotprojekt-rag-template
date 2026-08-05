"""Tests that ``POST /auth/register`` accepts a JSON body.

``app.py`` uses ``from __future__ import annotations``, so FastAPI receives the
handler's annotation as the *string* ``"RegisterRequest"`` and resolves it against
the module's globals. While the model was declared inside the ``on_app_startup``
hook the name was a local, resolution failed, and FastAPI did not raise: it
downgraded the parameter to a **query** parameter. The effect was that the
endpoint ignored every JSON body (422 with ``loc: ["query", "request"]``), a
query parameter reached the handler as a ``str`` and raised (500), and
``/openapi.json`` returned 500 because the schema could not be built.

The last test pins the failure mode itself, so the first two cannot pass
vacuously if FastAPI ever starts resolving locals.
"""

from __future__ import annotations

import os

# See tests/test_route_order.py: importing app.py registers a Chainlit
# oauth_callback, which refuses to load without a configured provider.
os.environ.setdefault("OAUTH_GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("OAUTH_GITHUB_CLIENT_SECRET", "test-client-secret")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import app as chainlit_app  # noqa: E402

RegisterRequest = chainlit_app.RegisterRequest


def _app_with_register_route() -> FastAPI:
    """Register a body-model route the same way app.py does."""
    fastapi_app = FastAPI()

    @fastapi_app.post("/auth/register")
    async def register_user(request: RegisterRequest):  # noqa: ANN202
        return {"username": request.username}

    return fastapi_app


def test_register_request_is_defined_at_module_level():
    """The actual regression guard: moving the class back into a function breaks the route."""
    assert isinstance(getattr(chainlit_app, "RegisterRequest", None), type)
    assert issubclass(chainlit_app.RegisterRequest, BaseModel)
    assert set(chainlit_app.RegisterRequest.model_fields) == {"username", "email", "password"}


def test_openapi_schema_builds():
    """/openapi.json returned 500 while the annotation was an unresolved forward ref."""
    schema = _app_with_register_route().openapi()
    assert "RegisterRequest" in schema["components"]["schemas"]


def test_register_takes_a_json_body_not_a_query_parameter():
    schema = _app_with_register_route().openapi()
    operation = schema["paths"]["/auth/register"]["post"]

    assert "requestBody" in operation
    # The bug's signature: a query parameter literally named "request".
    assert not [p for p in operation.get("parameters", []) if p["in"] == "query"]


def test_body_is_actually_parsed():
    client = TestClient(_app_with_register_route())

    assert client.post(
        "/auth/register",
        json={"username": "someone", "email": "a@b.c", "password": "longenough"},
    ).json() == {"username": "someone"}

    # Missing fields must be reported against the body, not against the query string.
    detail = client.post("/auth/register", json={}).json()["detail"]
    assert {d["loc"][0] for d in detail} == {"body"}


def test_create_user_issues_one_on_conflict_clause():
    """Postgres allows a single ON CONFLICT per statement.

    ``create_user`` had one clause per unique constraint (identifier, then email),
    which is a syntax error, so every registration failed with a 500 even once the
    body parsed. One untargeted ``ON CONFLICT DO NOTHING`` covers both constraints.
    """
    import asyncio
    import inspect

    import native_chat

    captured: list[str] = []

    class FakeConnection:
        async def fetchrow(self, sql, *args):  # noqa: ANN001, ANN202
            captured.append(sql)
            return None

        async def close(self):  # noqa: ANN202
            return None

    async def fake_connect(_url):  # noqa: ANN001, ANN202
        return FakeConnection()

    original = native_chat.asyncpg.connect
    native_chat.asyncpg.connect = fake_connect
    try:
        asyncio.run(native_chat.create_user("postgresql://x/y", "u", "e@x.y", "hash"))
    finally:
        native_chat.asyncpg.connect = original

    assert captured, "create_user did not execute a statement"
    # Count executable clauses only: the statement carries a `--` comment that
    # explains the bug and mentions ON CONFLICT itself.
    executable = "\n".join(
        line for line in captured[0].splitlines() if not line.strip().startswith("--")
    )
    assert executable.upper().count("ON CONFLICT") == 1
    assert inspect.isfunction(native_chat.create_user)


def test_a_local_body_model_would_still_break():
    """Pins the mechanism, so the tests above are not vacuous.

    A model defined in a function body is not in module globals, so the string
    annotation cannot be resolved. FastAPI treats the parameter as a scalar query
    parameter instead of failing loudly.
    """

    def build() -> FastAPI:
        class LocalBody(BaseModel):
            username: str

        fastapi_app = FastAPI()

        @fastapi_app.post("/local")
        async def handler(request: LocalBody):  # noqa: ANN202
            return {"ok": True}

        return fastapi_app

    local_app = build()
    detail = TestClient(local_app).post("/local", json={"username": "x"}).json()["detail"]
    assert detail[0]["loc"] == ["query", "request"]
