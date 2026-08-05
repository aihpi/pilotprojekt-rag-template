"""A failed run has to say what to do, not print a traceback.

A reported ingest failure was ~90% litellm's "Give Feedback / Get Help" blocks, ending
in a forty-line traceback through httpx, openai and litellm whose last line was
"Connection error." Nothing in it said what to check, and nothing mentioned that a
tool exists to test the connection.
"""

from __future__ import annotations

import litellm

import llm
from kb.ingest import _report_failure


def test_litellm_noise_is_switched_off():
    """Each failed call otherwise prints five lines of advertising. During an ingest
    that is hundreds of them, burying the lines that name the failing document."""
    assert litellm.suppress_debug_info is True


def test_embeddings_are_retried():
    """A failed figure description costs one figure and is caught. A failed embedding
    used to abort the whole run, throwing away work already paid for."""
    captured: dict = {}

    def fake_embedding(**kwargs):
        captured.update(kwargs)

        class Response:
            data = [{"index": 0, "embedding": [0.1, 0.2]}]

        return Response()

    original = litellm.embedding
    litellm.embedding = fake_embedding
    try:
        llm.embed_sync(["text"])
    finally:
        litellm.embedding = original

    assert captured.get("num_retries") == llm._EMBED_RETRIES
    assert llm._EMBED_RETRIES >= 1


def test_a_failure_prints_steps_and_points_at_the_check(capsys):
    _report_failure(RuntimeError("Error code: 401 - Authentication Error"))

    out = capsys.readouterr().out
    assert "stopped early" in out
    assert "The access key was rejected" in out
    assert "1. Open apps/chainlit/.env" in out
    assert "make check" in out, "must name the tool that tests the connection"
    assert "troubleshooting" in out


def test_an_unreachable_address_is_not_blamed_on_the_network(monkeypatch, capsys):
    """The same bug this had in check_setup: litellm reports a typo in the address and
    a mid-request disconnect identically, so the reporter must probe the host itself."""
    import check_setup

    def unreachable(_url):
        result = check_setup.Result(name="Reaching the AI service")
        result.error = "the name nope.invalid could not be looked up"
        return result

    monkeypatch.setattr(check_setup, "check_service_host", unreachable)
    _report_failure(RuntimeError("OpenAIException - Connection error."))

    out = capsys.readouterr().out
    assert "could not be reached at all" in out
    assert "LITELLM_BASE_URL" in out
    assert "VPN" not in out, "a wrong address must not send people hunting a VPN"
    assert "could not be looked up" in out, "the actual finding must be shown"


def test_the_reporter_never_swallows_an_unexpected_error(monkeypatch, capsys):
    """If the advice machinery itself breaks, the original error must still surface."""
    import check_setup

    def explode(*_args, **_kwargs):
        raise ValueError("reporter is broken")

    monkeypatch.setattr(check_setup, "_classify", explode)
    _report_failure(RuntimeError("the original problem"))

    out = capsys.readouterr().out + capsys.readouterr().err
    assert "the error" in out


def test_it_does_not_promise_that_work_was_saved(capsys):
    """The manifest is written only after a successful ingest, so a crashed run really
    does have to be repeated. Claiming otherwise would be a lie people plan around."""
    _report_failure(RuntimeError("Connection error."))

    out = capsys.readouterr().out
    assert "read again" in out
    assert "earlier runs are untouched" in out
