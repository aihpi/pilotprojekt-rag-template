"""The headless replay must behave like the app's loop where it matters.

Three properties carry the benchmark's meaning, and each would fail silently:
the tool loop must terminate and deduplicate (a runaway loop burns the gateway
mid-benchmark), the multi-turn history must grow with the *replayed* model's own
answers (feeding the gold answers back instead would measure a conversation that
never happened), and the judge must stay pinned across models (each model grading
itself voids the comparison).

Everything is faked at the same seams the other suites use: ``llm.chat`` for the
model, a hand-rolled router for the tools, ``httpx.AsyncClient`` for the service.
No network, no qdrant, no ragas.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

import benchmark
from config.schema import RagConfig


# --------------------------------------------------------------------------- #
# Fakes: litellm-shaped messages, a recording tool, a recording HTTP client
# --------------------------------------------------------------------------- #


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function
    type: str = "function"


@dataclass
class _Message:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


def _response(message: _Message):
    class _Choice:
        pass

    choice = _Choice()
    choice.message = message

    class _Response:
        choices = [choice]

    return _Response()


@dataclass
class _Result:
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class _FakeTool:
    """Counts handler invocations, so the dedupe cache is observable."""

    def __init__(self):
        self.calls = 0

    async def handler(self, args, ctx):
        self.calls += 1
        # The real tools do exactly this (tools/search.py) — it is the contract the
        # loop must honour, and passing filters=None broke it in the first live run.
        dict(ctx.filters)

        class _ToolResult:
            results = [_Result(text="Die Rate lag bei 40-60%.", score=0.9,
                               metadata={"source_file": "kage.pdf", "page": 4})]
            payload = {"context": "[1] Die Rate lag bei 40-60%."}

        return _ToolResult()


def _search_call(call_id: str = "c1") -> _ToolCall:
    return _ToolCall(id=call_id, function=_Function("search", json.dumps({"query": "rate"})))


def _install_chat(monkeypatch, script: list[_Message]):
    """``llm.chat`` replayed from a script; records every call's arguments."""
    calls: list[dict[str, Any]] = []
    remaining = list(script)

    async def fake_chat(messages, tools=None, tool_choice=None, model=None):
        calls.append({"messages": [dict(m) for m in messages], "model": model,
                      "tools": tools, "tool_choice": tool_choice})
        return _response(remaining.pop(0) if remaining else _Message(content="leer"))

    monkeypatch.setattr(benchmark, "chat", fake_chat)
    return calls


CFG = RagConfig(evaluation={"enabled": True})


# --------------------------------------------------------------------------- #
# The tool loop
# --------------------------------------------------------------------------- #


def test_the_loop_runs_tools_then_returns_the_final_answer(monkeypatch):
    tool = _FakeTool()
    calls = _install_chat(monkeypatch, [
        _Message(tool_calls=[_search_call()]),
        _Message(content="Zwischen 40 und 60 Prozent. Quelle 1"),
    ])

    answer, results = asyncio.run(benchmark.answer_question(
        "Wie hoch war die Rate?", [], model="gemma-4-31b", cfg=CFG,
        schemas=[], router={"search": tool},
    ))

    assert answer == "Zwischen 40 und 60 Prozent. Quelle 1"
    assert len(results) == 1 and tool.calls == 1
    assert calls[0]["tool_choice"] == "required", "the first call must force retrieval"


def test_identical_tool_calls_hit_the_cache_not_the_tool(monkeypatch):
    tool = _FakeTool()
    _install_chat(monkeypatch, [
        _Message(tool_calls=[_search_call("c1"), _search_call("c2")]),
        _Message(content="Antwort."),
    ])

    asyncio.run(benchmark.answer_question(
        "Frage?", [], model="m", cfg=CFG, schemas=[], router={"search": tool},
    ))

    assert tool.calls == 1, "the second identical call must come from the cache"


def test_the_round_cap_forces_a_final_answer(monkeypatch):
    tool = _FakeTool()
    # The model never stops asking for tools; the forced-final call (the one made
    # WITHOUT tools) must end it. 13 tool-call responses feed the first call plus
    # the 12 in-loop follow-ups; the 14th response answers the forced final.
    endless = [_Message(tool_calls=[_search_call(f"c{i}")]) for i in range(13)]
    calls = _install_chat(monkeypatch, endless + [_Message(content="Erzwungene Antwort.")])

    answer, _ = asyncio.run(benchmark.answer_question(
        "Frage?", [], model="m", cfg=CFG, schemas=[], router={"search": tool},
    ))

    # 1 first call + 12 in-loop follow-ups, then the forced final.
    forced = calls[-1]
    assert forced["tools"] is None, "the forced final must not offer tools again"
    assert answer != "" and "Erzwungene" in answer


def test_an_unknown_tool_becomes_an_error_payload_not_a_crash(monkeypatch):
    _install_chat(monkeypatch, [
        _Message(tool_calls=[_ToolCall(id="c1", function=_Function("nope", "{}"))]),
        _Message(content="Antwort."),
    ])

    answer, results = asyncio.run(benchmark.answer_question(
        "Frage?", [], model="m", cfg=CFG, schemas=[], router={},
    ))
    assert answer == "Antwort." and results == []


# --------------------------------------------------------------------------- #
# Multi-turn replay: run_job
# --------------------------------------------------------------------------- #

GOLD = [{
    "id": "g-1",
    "turns": [
        {"user": "Frage eins?", "assistant": "Gold-Antwort eins."},
        {"user": "Frage zwei?", "assistant": "Gold-Antwort zwei."},
    ],
}]


def _install_service(monkeypatch, *, gold=None):
    """Fake httpx: serves the gold set, records every POST."""
    posts: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    class _Fake:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return _Response({"gold": gold if gold is not None else GOLD})

        async def post(self, url, json=None):
            posts.append((url, json or {}))
            return _Response({})

    monkeypatch.setattr(benchmark.httpx, "AsyncClient", _Fake)
    return posts


def _run(monkeypatch, chat_calls_script, *, job=None):
    monkeypatch.setattr(benchmark, "get_config", lambda: CFG)
    monkeypatch.setattr(benchmark, "build_openai_tools", lambda cfg: ([], {}))
    monkeypatch.setattr(benchmark, "_system_prompt", lambda cfg: "Du bist ein Assistent.")
    chat_calls = _install_chat(monkeypatch, chat_calls_script)
    posts = _install_service(monkeypatch)
    summary = asyncio.run(benchmark.run_job(
        job or {"id": "j-1", "chat_model": "gemma-4-31b", "judge_model": None,
                "run_label": "run-1"},
        service_url="http://eval:8001",
    ))
    return chat_calls, posts, summary


def test_the_history_grows_with_the_replayed_answers_not_the_gold_ones(monkeypatch):
    chat_calls, posts, summary = _run(monkeypatch, [
        _Message(content="Replay-Antwort eins."),
        _Message(content="Replay-Antwort zwei."),
    ])

    # The second turn's request must contain the REPLAYED first answer: drift is
    # the thing a conversation benchmark measures.
    second_turn_messages = chat_calls[1]["messages"]
    assistant_texts = [m["content"] for m in second_turn_messages if m["role"] == "assistant"]
    assert assistant_texts == ["Replay-Antwort eins."]
    assert not any("Gold-Antwort eins." == t for t in assistant_texts)
    assert summary == {"total": 2, "done": 2, "failed": 0}


def test_every_turn_posts_its_reference_and_provenance(monkeypatch):
    _, posts, _ = _run(monkeypatch, [
        _Message(content="Replay-Antwort eins."),
        _Message(content="Replay-Antwort zwei."),
    ])

    score_posts = [body for url, body in posts if url.endswith("/api/score")]
    assert [p["gold_turn"] for p in score_posts] == [1, 2]
    assert [p["reference"] for p in score_posts] == ["Gold-Antwort eins.", "Gold-Antwort zwei."]
    assert all(p["source"] == "replay" and p["run_label"] == "run-1" for p in score_posts)
    assert all(p["metrics"] == ["faithfulness", "relevance", "similarity"] for p in score_posts)


def test_the_judge_is_pinned_and_never_the_replayed_model(monkeypatch):
    _, posts, _ = _run(monkeypatch, [
        _Message(content="eins."), _Message(content="zwei."),
    ])

    score_posts = [body for url, body in posts if url.endswith("/api/score")]
    judges = {p["judge_model"] for p in score_posts}
    assert judges == {CFG.models.chat_model}, (
        "judge_model: null resolves to the configured default, not to gemma-4-31b — "
        "a model grading itself would void the comparison"
    )


def test_a_failed_turn_is_counted_and_the_conversation_stays_on_track(monkeypatch):
    fail_first = {"n": 0}

    async def flaky_chat(messages, tools=None, tool_choice=None, model=None):
        fail_first["n"] += 1
        if fail_first["n"] == 1:
            raise RuntimeError("gateway 500")
        return _response(_Message(content="Replay-Antwort zwei."))

    monkeypatch.setattr(benchmark, "get_config", lambda: CFG)
    monkeypatch.setattr(benchmark, "build_openai_tools", lambda cfg: ([], {}))
    monkeypatch.setattr(benchmark, "_system_prompt", lambda cfg: None)
    monkeypatch.setattr(benchmark, "chat", flaky_chat)
    posts = _install_service(monkeypatch)

    summary = asyncio.run(benchmark.run_job(
        {"id": "j-1", "chat_model": "m", "judge_model": None, "run_label": "r"},
        service_url="http://eval:8001",
    ))

    assert summary == {"total": 2, "done": 2, "failed": 1}
    score_posts = [body for url, body in posts if url.endswith("/api/score")]
    assert len(score_posts) == 1, "the failed turn posts nothing"
    # The job closes as done, carrying the failure count for the dashboard tooltip.
    status_posts = [body for url, body in posts if url.endswith("/api/benchmark/j-1")]
    assert status_posts[-1]["status"] == "done" and "1" in (status_posts[-1]["error"] or "")
