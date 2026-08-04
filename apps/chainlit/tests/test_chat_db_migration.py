"""Moving the chat history off the bind mount, without losing or spreading damage.

The database used to live in `.chainlit/`, which Docker bind-mounts from the host.
SQLite in WAL mode needs a shared-memory file and real POSIX locking, and Docker
Desktop only emulates both across the macOS/Windows filesystem boundary. A write
interrupted at the wrong moment produced "database disk image is malformed", which
is exactly what happened here after a series of container restarts.

Under Docker the database now lives on a named volume, a real Linux filesystem
inside the VM. These tests cover the one-time hand-over.
"""

from __future__ import annotations

import gc
import sqlite3
from pathlib import Path

from chat_history import (
    add_chat_message,
    create_chat_session,
    init_chat_db,
    list_chat_sessions,
    migrate_legacy_db,
)


def _populated_db(path: Path, session_id: str = "s1") -> Path:
    init_chat_db(path)
    create_chat_session(path, session_id, user_id="admin")
    add_chat_message(path, session_id, "user", "hello")
    return path


def _corrupt(path: Path) -> None:
    """Damage the file the way the real failure did: a torn main database.

    Drops the WAL companions and scribbles over the header pages, so nothing can be
    reconstructed. No checkpoint first, because ``chat_history`` uses
    ``with sqlite3.connect(...)``, which commits but never closes, so its connections
    are still holding locks at this point and a checkpoint would fail.
    """
    gc.collect()  # release those leaked connections where the platform needs it
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    with path.open("r+b") as fh:
        fh.write(b"\x00" * 512)


def test_an_existing_history_is_carried_over(tmp_path):
    legacy = _populated_db(tmp_path / "old" / "chat_history.sqlite3")
    target = tmp_path / "volume" / "chat_history.sqlite3"

    migrate_legacy_db(target, legacy)

    assert target.exists()
    assert len(list_chat_sessions(target)) == 1
    # The old file is left alone, so a mistake here is not destructive.
    assert legacy.exists()


def test_a_corrupt_history_is_not_copied(tmp_path):
    """Carrying the damage across would defeat the point of moving."""
    legacy = _populated_db(tmp_path / "old" / "chat_history.sqlite3")
    _corrupt(legacy)
    target = tmp_path / "volume" / "chat_history.sqlite3"

    migrate_legacy_db(target, legacy)

    assert not target.exists(), "a damaged file must not be propagated"
    assert legacy.exists(), "and must be kept, so it can still be recovered"


def test_the_recovery_command_is_printed_for_a_corrupt_file(tmp_path, capsys):
    legacy = _populated_db(tmp_path / "old" / "chat_history.sqlite3")
    _corrupt(legacy)

    migrate_legacy_db(tmp_path / "volume" / "chat_history.sqlite3", legacy)

    out = capsys.readouterr().out
    assert ".recover" in out, "must say how to rescue the messages"
    assert str(legacy) in out


def test_an_existing_target_is_never_overwritten(tmp_path):
    """Otherwise every restart would trample the live database with the old one."""
    legacy = _populated_db(tmp_path / "old" / "chat_history.sqlite3", "from_legacy")
    target = _populated_db(tmp_path / "volume" / "chat_history.sqlite3", "already_here")

    migrate_legacy_db(target, legacy)

    ids = {s["id"] for s in list_chat_sessions(target)}
    assert ids == {"already_here"}


def test_nothing_happens_without_a_legacy_file(tmp_path):
    target = tmp_path / "volume" / "chat_history.sqlite3"
    migrate_legacy_db(target, tmp_path / "old" / "chat_history.sqlite3")
    assert not target.exists()


def test_identical_paths_are_a_no_op(tmp_path):
    """The non-Docker default has both pointing at the same file."""
    path = _populated_db(tmp_path / "chat_history.sqlite3")
    before = path.read_bytes()

    migrate_legacy_db(path, path)

    assert path.read_bytes() == before


def test_the_migrated_database_is_writable(tmp_path):
    """A copied file must still accept new messages, not just reads."""
    legacy = _populated_db(tmp_path / "old" / "chat_history.sqlite3")
    target = tmp_path / "volume" / "chat_history.sqlite3"
    migrate_legacy_db(target, legacy)

    init_chat_db(target)
    create_chat_session(target, "after_move", user_id="admin")
    add_chat_message(target, "after_move", "user", "still works")

    assert {s["id"] for s in list_chat_sessions(target)} == {"s1", "after_move"}
    with sqlite3.connect(target) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
