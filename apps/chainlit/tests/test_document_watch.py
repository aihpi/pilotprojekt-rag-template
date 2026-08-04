"""The folder watcher: cheap detection, settle delay, and one pass at a time.

The watcher exists so that putting a file into the documents folder is enough. It
polls rather than using filesystem events, because event delivery from a host bind
mount into a container is unreliable and a watcher that silently stops noticing is
worse than one that looks every few seconds.

Detection is deliberately two-stage. The cheap stage compares size and modification
time and reads no file contents, which is what makes polling every few seconds
sensible at all: hashing the whole corpus on a timer would be pure waste.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("OAUTH_GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("OAUTH_GITHUB_CLIENT_SECRET", "test-client-secret")

import pytest  # noqa: E402

import document_watch  # noqa: E402
from config.schema import ChunkingConfig, DataSourceConfig, RagConfig  # noqa: E402
from kb.parsers.base import FileGate, file_gate, iter_source_files  # noqa: E402


def _config_at(dir_path, **kw) -> RagConfig:
    cfg = RagConfig(**kw)
    cfg._config_dir = dir_path
    return cfg


def _text_config(tmp_path) -> RagConfig:
    return _config_at(
        tmp_path,
        data_sources=[DataSourceConfig(name="docs", path="docs", format="txt", glob="*.txt")],
        chunking=ChunkingConfig(strategy="passthrough"),
    )


# --------------------------------------------------------------------------- #
# The cheap stage
# --------------------------------------------------------------------------- #
def test_stat_only_records_without_reading_contents(tmp_path, monkeypatch):
    """A poll must not hash. Proven by making hashing fail loudly."""
    target = tmp_path / "a.txt"
    target.write_text("content", encoding="utf-8")

    def explode(_path):
        raise AssertionError("stat_only must not hash file contents")

    monkeypatch.setattr("kb.parsers.base.file_digest", explode)
    gate = FileGate(skip_all=True, stat_only=True, root=tmp_path)

    assert gate.admit([target]) == []
    assert list(gate.seen) == ["a.txt"]
    assert ":" in gate.seen["a.txt"], "token should be size:mtime"


def test_stat_token_changes_when_the_file_changes(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("first", encoding="utf-8")
    before = FileGate(skip_all=True, stat_only=True, root=tmp_path)
    before.admit([target])

    target.write_text("a different length of content", encoding="utf-8")
    after = FileGate(skip_all=True, stat_only=True, root=tmp_path)
    after.admit([target])

    assert after.seen["a.txt"] != before.seen["a.txt"]


def test_a_vanished_file_does_not_crash_the_poll(tmp_path):
    """Listing and stat are not atomic; a file can disappear in between."""
    gate = FileGate(skip_all=True, stat_only=True, root=tmp_path)
    assert gate.admit([tmp_path / "never_existed.txt"]) == []
    assert gate.seen == {}


def test_snapshot_covers_every_source_through_the_parsers(tmp_path, monkeypatch):
    """The snapshot reuses plan_ingest, so it cannot drift from what parsers read."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (docs / "b.txt").write_text("beta", encoding="utf-8")
    config = _text_config(tmp_path)
    monkeypatch.setattr(document_watch, "get_config", lambda: config)

    snapshot = document_watch._snapshot()

    assert set(snapshot) == {"docs/a.txt", "docs/b.txt"}


# --------------------------------------------------------------------------- #
# Settle delay
# --------------------------------------------------------------------------- #
def test_a_file_written_right_now_is_held_back(monkeypatch):
    """A large PDF is visible long before the copy finishes."""
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 5)
    now = 1_000_000_000_000_000_000

    settled = document_watch._settled(
        {
            "fresh.pdf": f"100:{now - 1_000_000_000}",  # 1 second old
            "old.pdf": f"100:{now - 60_000_000_000}",  # 60 seconds old
        },
        now,
    )

    assert list(settled) == ["old.pdf"]


def test_a_malformed_token_is_not_allowed_to_stall_the_watcher(monkeypatch):
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 5)
    settled = document_watch._settled({"weird.pdf": "not-a-token"}, 1_000)
    assert list(settled) == ["weird.pdf"]


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #
@pytest.fixture
def loop_harness(monkeypatch):
    """Drive watch_documents deterministically: fixed snapshots, no sleeping."""
    calls: list[int] = []
    snapshots: list[dict[str, str]] = []

    async def fake_ingest():
        calls.append(1)
        return {"ingested": 1, "pruned": 0}

    def next_snapshot():
        return snapshots.pop(0) if snapshots else {}

    monkeypatch.setattr(document_watch, "_snapshot", next_snapshot)
    monkeypatch.setattr(document_watch, "_ingest_now", fake_ingest)
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 0)
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_INTERVAL", 0)
    return calls, snapshots


def _run_passes(n: int):
    """Run the watcher for n polls, then stop it."""

    async def main():
        task = asyncio.create_task(document_watch.watch_documents())
        for _ in range(n * 4):  # generous: let the loop yield through its awaits
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())


def test_the_first_pass_only_learns_the_state(loop_harness):
    """The startup ingest has already run, so acting on pass one would repeat it."""
    calls, snapshots = loop_harness
    snapshots.extend([{"a.txt": "1:1"}, {"a.txt": "1:1"}])

    _run_passes(2)

    assert calls == [], "an unchanged folder must never trigger an ingest"


def test_a_change_triggers_exactly_one_ingest(loop_harness):
    calls, snapshots = loop_harness
    snapshots.extend([
        {"a.txt": "1:1"},              # pass 1: baseline
        {"a.txt": "1:1", "b.txt": "2:2"},  # pass 2: b.txt appeared -> ingest
        {"a.txt": "1:1", "b.txt": "2:2"},  # re-read after the ingest
        {"a.txt": "1:1", "b.txt": "2:2"},  # pass 3: nothing new
    ])

    _run_passes(3)

    assert calls == [1], f"expected one ingest, got {len(calls)}"


def test_a_deletion_triggers_an_ingest_too(loop_harness):
    """The folder is the source of truth, so removals must be acted on as well."""
    calls, snapshots = loop_harness
    snapshots.extend([
        {"a.txt": "1:1", "b.txt": "2:2"},
        {"a.txt": "1:1"},
        {"a.txt": "1:1"},
        {"a.txt": "1:1"},
    ])

    _run_passes(3)

    assert calls == [1]


def test_an_edit_triggers_an_ingest(loop_harness):
    calls, snapshots = loop_harness
    snapshots.extend([
        {"a.txt": "1:1"},
        {"a.txt": "9:9"},
        {"a.txt": "9:9"},
        {"a.txt": "9:9"},
    ])

    _run_passes(3)

    assert calls == [1]


def test_a_failing_pass_does_not_kill_the_watcher(monkeypatch):
    """A watcher that dies on one error stops noticing changes, silently."""
    attempts: list[int] = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("qdrant briefly unreachable")
        return {}

    monkeypatch.setattr(document_watch, "_snapshot", flaky)
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_INTERVAL", 0)
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 0)

    _run_passes(3)

    assert len(attempts) >= 2, "the loop must keep polling after an error"
