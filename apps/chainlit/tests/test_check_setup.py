"""The setup check has to point at the right culprit, or it is worse than nothing.

Written after the first version got two cases wrong against a live gateway: an
address that does not resolve was blamed on "a VPN in between", and one of two
identical authentication failures fell through to "see the message above". Both are
pinned here.
"""

from __future__ import annotations

import check_setup
from check_setup import Result, _classify, check_service_host, summarise


def _exc(message: str) -> Exception:
    return RuntimeError(message)


# --------------------------------------------------------------------------- #
# Reading an error
# --------------------------------------------------------------------------- #
def test_a_dropped_connection_blames_the_network_only_when_the_host_answers():
    """The regression: litellm reports a DNS failure and a mid-request disconnect
    identically as "Connection error", so the message alone must not decide."""
    dropped = _exc("litellm.InternalServerError: OpenAIException - Connection error.")

    reachable = _classify(dropped, host_reachable=True)
    unreachable = _classify(dropped, host_reachable=False)

    assert "network in between" in reachable
    assert "VPN" in reachable
    assert "could not be reached" in unreachable
    assert "VPN" not in unreachable, "a wrong address must not send people hunting a VPN"
    assert "LITELLM_BASE_URL" in unreachable


def test_every_shape_of_auth_failure_blames_the_key():
    """The second regression: this gateway words 401 several ways."""
    for message in (
        "Error code: 401 - {'error': {'message': 'Authentication Error, Invalid proxy...",
        "AuthenticationError: Invalid proxy server token passed.",
        "openai.AuthenticationError: Unauthorized",
        "Invalid API key provided",
    ):
        assert "key was rejected" in _classify(_exc(message)), message


def test_an_unknown_model_is_not_confused_with_a_bad_key():
    hint = _classify(_exc("Error code: 404 - model 'gemma-9' does not exist"))
    assert "does not know this model name" in hint


def test_rate_limiting_says_to_wait():
    assert "rate limiting" in _classify(_exc("Error code: 429 - rate limit exceeded"))


def test_a_forbidden_response_mentions_the_model_name_spelling():
    """403 here usually means the model name needs a different form for this gateway."""
    hint = _classify(_exc("Error code: 403 - Forbidden"))
    assert "written differently" in hint


def test_an_unrecognised_error_does_not_invent_a_cause():
    assert _classify(_exc("something entirely unexpected")) == "See the message above."


# --------------------------------------------------------------------------- #
# Reaching the host
# --------------------------------------------------------------------------- #
def test_a_name_that_does_not_resolve_is_reported_as_such():
    result = check_service_host("https://this-host-does-not-exist.invalid/")

    assert not result.ok
    assert "could not be looked up" in result.error
    assert "LITELLM_BASE_URL" in result.hint


def test_a_missing_address_is_reported_before_anything_is_tried():
    result = check_service_host("")
    assert not result.ok
    assert "not set" in result.error


def test_a_url_without_a_host_is_rejected_clearly():
    result = check_service_host("not-a-url")
    assert not result.ok
    assert "cannot read a host" in result.error


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #
def _ok(name: str) -> Result:
    return Result(name=name, ok=True, attempts=5, passed=5, seconds=[0.1] * 5)


def _flaky(name: str) -> Result:
    return Result(name=name, attempts=5, passed=3, seconds=[0.1] * 3, error="dropped")


def _broken(name: str) -> Result:
    return Result(name=name, attempts=5, passed=0, error="nope", hint="do this")


def test_all_green_exits_zero(capsys):
    assert summarise([_ok("a"), _ok("b")]) == 0
    assert "All good" in capsys.readouterr().out


def test_partial_success_is_called_out_as_a_connection_problem(capsys):
    """The case from the reported log: most calls work, many do not."""
    code = summarise([_ok("chat"), _flaky("search")])

    out = capsys.readouterr().out
    assert code == 1
    assert "not every time" in out
    assert "stable internet connection" in out
    assert "hundreds of calls" in out, "must explain why ingest suffers more than chat"


def test_a_hard_failure_exits_non_zero_and_prints_the_advice(capsys):
    code = summarise([_broken("search")])

    out = capsys.readouterr().out
    assert code == 1
    assert "what to do" in out
    assert "do this" in out


def test_skipped_checks_do_not_count_against_the_verdict(capsys):
    skipped = Result(name="Image model", skipped="not needed, images.mode is 'none'")

    assert summarise([_ok("chat"), skipped]) == 0


def test_the_line_for_each_state_is_readable():
    assert "[ ok ]" in _ok("x").line()
    assert "[WARN]" in _flaky("x").line() and "only 3 of 5" in _flaky("x").line()
    assert "[FAIL]" in _broken("x").line()
    assert "[skip]" in Result(name="x", skipped="because").line()


def test_attempts_are_repeated_enough_to_reveal_flakiness():
    """One attempt cannot distinguish "broken" from "unreliable", which is the whole
    point of the tool."""
    assert check_setup.ATTEMPTS >= 3
