"""`models.fallback_chat_model` has to reach litellm, not just exist in the config.

It was declared in the schema, read into settings and mapped as an env override,
and then never passed to a call — so a gateway outage of one model group took
down answering and startup prompt generation, and the app quietly served the
bundled default prompt instead of one written from the corpus.
"""

from __future__ import annotations

import asyncio

import pytest

import llm


@pytest.fixture
def captured(monkeypatch):
    """Intercept the litellm call and hand back its kwargs."""
    seen: dict = {}

    async def fake_acompletion(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(llm.litellm, "acompletion", fake_acompletion)
    return seen


def test_the_configured_fallback_is_passed_to_litellm(monkeypatch, captured):
    monkeypatch.setattr(llm, "FALLBACK_CHAT_MODEL", "gemma-4-31b")
    asyncio.run(llm.chat([{"role": "user", "content": "hi"}], model="gpt-oss-120b"))

    assert captured["model"] == "gpt-oss-120b"
    assert captured["fallbacks"] == ["gemma-4-31b"]


def test_no_fallback_key_when_none_is_configured(monkeypatch, captured):
    """The default is null, and litellm should see the call exactly as before."""
    monkeypatch.setattr(llm, "FALLBACK_CHAT_MODEL", None)
    asyncio.run(llm.chat([{"role": "user", "content": "hi"}]))

    assert "fallbacks" not in captured


def test_a_model_is_never_its_own_fallback(monkeypatch, captured):
    """Asking for gemma when gemma is the fallback would otherwise retry the model
    that just failed, turning one outage into two identical attempts."""
    monkeypatch.setattr(llm, "FALLBACK_CHAT_MODEL", "gemma-4-31b")
    asyncio.run(llm.chat([{"role": "user", "content": "hi"}], model="gemma-4-31b"))

    assert "fallbacks" not in captured


def test_streaming_answers_get_the_same_fallback(monkeypatch, captured):
    """Answering is the streaming path; a fallback that only covered the
    non-streaming one would miss the case users actually hit."""
    monkeypatch.setattr(llm, "FALLBACK_CHAT_MODEL", "gemma-4-31b")
    monkeypatch.setattr(llm, "CHAT_MODEL", "gpt-oss-120b")
    asyncio.run(llm.stream_chat([{"role": "user", "content": "hi"}]))

    assert captured["stream"] is True
    assert captured["fallbacks"] == ["gemma-4-31b"]
