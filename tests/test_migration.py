import sqlite3
from pathlib import Path

import pytest

from owlmatic.domain import CheckCounts, Run
from owlmatic.errors import OwlError
from owlmatic.infrastructure.run_repository import SqliteRuns
from owlmatic.infrastructure.sqlite import SqliteDatabase
from owlmatic.serialization import encode
from tests.fakes import FakeClock
from tests.test_storage import invocation


def test_v1_migration_preserves_runs_and_locks_and_fences_legacy_workers(tmp_path: Path) -> None:
    active = invocation(1)
    terminal = invocation(2).finish(
        state="completed",
        outcome="pass",
        effects="completed",
        finished_at="fixed",
        checks=CheckCounts(required=1, passed=1, failed=0, skipped=0),
    )
    with sqlite3.connect(tmp_path / "catalog.sqlite") as db:
        db.execute(
            "CREATE TABLE runs(id TEXT PRIMARY KEY,data TEXT NOT NULL,pid INTEGER,heartbeat REAL,request_id TEXT UNIQUE,fingerprint TEXT)"
        )
        db.execute("CREATE TABLE locks(key TEXT PRIMARY KEY,run_id TEXT NOT NULL)")
        db.execute("PRAGMA user_version=1")
        for run in (active, terminal):
            db.execute("INSERT INTO runs(id,data,heartbeat) VALUES(?,?,?)", (run.run_id, encode(run), 1000))
        db.execute("INSERT INTO locks VALUES('resource',?)", (active.run_id,))
    database = SqliteDatabase(tmp_path)
    runs = SqliteRuns(database, FakeClock())
    migrated = runs.get(active.run_id)
    assert migrated.state == "error" and migrated.effects == "unknown"
    assert migrated.error and migrated.error.code == "UPGRADE_INTERRUPTED"
    assert runs.get(terminal.run_id) == terminal
    assert runs.is_pending(active.run_id) and runs.is_pending(terminal.run_id)
    with database.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT run_id FROM locks").fetchone()[0] == active.run_id
    # Opening a migrated database again is idempotent.
    SqliteDatabase(tmp_path)
    with pytest.raises(OwlError) as caught:
        runs.claim(active.run_id, "legacy-retry")
    assert caught.value.code == "LEASE_LOST"


def test_unknown_schema_version_is_not_modified(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "catalog.sqlite") as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(OwlError) as caught:
        SqliteDatabase(tmp_path)
    assert caught.value.code == "DATABASE_VERSION"
    with sqlite3.connect(tmp_path / "catalog.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


def test_database_contention_has_stable_error(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path)
    with database.connect() as first:
        first.execute("BEGIN IMMEDIATE")
        with pytest.raises(OwlError) as caught:
            with database.connect() as second:
                second.execute("PRAGMA busy_timeout=0")
                second.execute("BEGIN IMMEDIATE")
        assert caught.value.code == "STORAGE_BUSY"


def test_finished_run_requires_valid_final_state() -> None:
    # Persisted contracts must be validated when loaded, not assumed safe because
    # SQLite returned text. The transport converts this into a safe error.
    with pytest.raises(ValueError):
        Run.model_validate_json('{"run_id":"x","workflow_ref":"y","state":"completed"}')
