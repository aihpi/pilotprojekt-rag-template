"""The on-disk cache for figure descriptions.

A description costs a vision call, so an import that runs twice must not pay
twice. These tests pin the parts that are easy to get wrong: that a failed
description is never cached (which would make the failure permanent), that the
key really covers the image, prompt and model, and that a broken cache degrades
to the old behaviour instead of raising.

``XDG_CACHE_HOME`` is redirected at ``tmp_path`` in every test, so none of them
touch the real cache directory.
"""

from __future__ import annotations

import llm
import pytest


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """Redirect the cache at tmp_path and count the live calls behind it."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # Module-level "warn once" flag: without this reset, a test that trips the
    # warning would silence it for every test that runs after it.
    monkeypatch.setattr(llm, "_DESCRIBE_CACHE_WARNED", False, raising=False)

    calls: list[tuple[str, str, str]] = []
    result = {"text": "a bar chart"}

    def fake_sync(uri, prompt, model):
        calls.append((uri, prompt, model))
        return result["text"]

    monkeypatch.setattr(llm, "describe_image_sync", fake_sync)
    return calls, result, llm._describe_cache_dir()


def test_an_identical_second_call_does_not_reach_the_model(cache):
    calls, _result, _dir = cache
    first = llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma")
    second = llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma")
    assert first == second == "a bar chart"
    assert len(calls) == 1, "the second call should have been served from disk"


def test_a_failed_description_is_not_cached(cache):
    """An empty result means the call failed. Caching it would make the figure
    permanently undescribed, and the advice to re-ingest would stop working."""
    calls, result, cache_dir = cache
    result["text"] = ""
    assert llm.describe_image_cached("data:image/jpeg;base64,AAA", "p", "m") == ""
    assert list(cache_dir.glob("*.txt")) == []

    # A later run that succeeds gets through and is then cached: the second of
    # these two is a hit, so the failure cost one retry, not one per run forever.
    result["text"] = "recovered"
    assert llm.describe_image_cached("data:image/jpeg;base64,AAA", "p", "m") == "recovered"
    assert llm.describe_image_cached("data:image/jpeg;base64,AAA", "p", "m") == "recovered"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "uri,prompt,model",
    [
        ("data:image/jpeg;base64,BBB", "describe", "gemma"),  # figure changed
        ("data:image/jpeg;base64,AAA", "describe it fully", "gemma"),  # prompt changed
        ("data:image/jpeg;base64,AAA", "describe", "qwen-vl"),  # model changed
    ],
)
def test_changing_any_input_misses_the_cache(cache, uri, prompt, model):
    """describe_image_max_px is covered too: it changes the encoded bytes, so it
    changes the URI and therefore the key."""
    calls, _result, _dir = cache
    llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma")
    llm.describe_image_cached(uri, prompt, model)
    assert len(calls) == 2


def test_an_unreadable_entry_falls_back_to_the_model(cache, capsys):
    """Worst case is the old behaviour, never a crash. The entry is replaced with a
    directory, which fails the read for any user, unlike chmod under root."""
    calls, _result, cache_dir = cache
    llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma")
    entry = next(iter(cache_dir.glob("*.txt")))
    entry.unlink()
    entry.mkdir()

    assert llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma") == (
        "a bar chart"
    )
    assert len(calls) == 2
    assert "cache unavailable" in capsys.readouterr().out


def test_an_unwritable_cache_returns_descriptions_and_complains_once(cache, monkeypatch, capsys):
    """Every call still gets its description, and a corpus with hundreds of pictures
    does not print hundreds of identical complaints."""
    calls, _result, _dir = cache

    def refuse(*_a, **_kw):
        raise PermissionError("read-only file system")

    monkeypatch.setattr("pathlib.Path.mkdir", refuse)
    for i in range(5):
        assert llm.describe_image_cached(f"data:image/jpeg;base64,{i}", "p", "m") == "a bar chart"
    assert len(calls) == 5
    assert capsys.readouterr().out.count("cache unavailable") == 1


def test_a_corrupt_entry_is_re_fetched_instead_of_crashing_the_import(cache, capsys):
    """A torn write leaves bytes that are not UTF-8. read_text then raises
    UnicodeDecodeError, which is a ValueError and NOT an OSError, so catching only
    OSError let one bad entry abort an entire import."""
    calls, _result, cache_dir = cache
    llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma")
    entry = next(iter(cache_dir.glob("*.txt")))
    entry.write_bytes(b"\xff\xfe torn write")

    assert llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma") == (
        "a bar chart"
    )
    assert len(calls) == 2
    assert "cache unavailable" in capsys.readouterr().out


def test_concurrent_writers_do_not_share_a_temp_path(cache, monkeypatch):
    """os.getpid() cannot separate them: every container runs as PID 1, so two
    ingests on one cache volume would interleave into the same temp file."""
    seen: list[str] = []
    real = type(cache[2]).write_text

    def spy(self, data, *a, **kw):
        if self.suffix == ".tmp":
            seen.append(self.name)
        return real(self, data, *a, **kw)

    monkeypatch.setattr("pathlib.Path.write_text", spy)
    for i in range(4):
        llm.describe_image_cached(f"data:image/jpeg;base64,{i}", "p", "m")
    assert len(seen) == len(set(seen)) == 4, f"temp names collided: {seen}"


def test_a_zero_byte_entry_is_treated_as_a_miss(cache):
    """Nothing writes one, but a full disk or a botched copy could leave one. Serving
    it would mean "this figure has no description" forever."""
    calls, _result, cache_dir = cache
    llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma")
    entry = next(iter(cache_dir.glob("*.txt")))
    entry.write_text("")

    assert llm.describe_image_cached("data:image/jpeg;base64,AAA", "describe", "gemma") == (
        "a bar chart"
    )
    assert len(calls) == 2


def test_non_ascii_descriptions_round_trip(cache):
    """Descriptions are German in the shipped config, so the encoding matters."""
    calls, result, _dir = cache
    result["text"] = "Ein Säulendiagramm über Größenverhältnisse — 30 °C"
    first = llm.describe_image_cached("data:image/jpeg;base64,AAA", "p", "m")
    second = llm.describe_image_cached("data:image/jpeg;base64,AAA", "p", "m")
    assert first == second == "Ein Säulendiagramm über Größenverhältnisse — 30 °C"
    assert len(calls) == 1


def test_the_cache_lands_under_xdg_cache_home(cache, tmp_path):
    _calls, _result, cache_dir = cache
    llm.describe_image_cached("data:image/jpeg;base64,AAA", "p", "m")
    assert cache_dir == tmp_path / "rag-template" / "figure-descriptions"
    assert len(list(cache_dir.glob("*.txt"))) == 1
    assert list(cache_dir.glob("*.tmp")) == [], "temp files must not be left behind"
