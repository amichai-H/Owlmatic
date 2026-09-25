"""SQLite implementation of the catalog and run repository ports."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from ..domain import Workflow
from ..errors import OwlError
from ..serialization import encode
from .files import private_directory


class SqliteDatabase:
    def __init__(self, root: Path) -> None:
        self.path = private_directory(root) / "catalog.sqlite"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise OwlError("DATABASE_VERSION", "This database needs a different Owlmatic version")
            db.execute(
                "CREATE TABLE IF NOT EXISTS workflows (ref TEXT PRIMARY KEY, catalog TEXT NOT NULL, "
                "id TEXT NOT NULL, data TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
            )
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS workflow_search USING fts5("
                "ref UNINDEXED, name, description, aliases, examples)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, data TEXT NOT NULL, pid INTEGER, "
                "heartbeat REAL, request_id TEXT UNIQUE, fingerprint TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS locks (key TEXT PRIMARY KEY, run_id TEXT NOT NULL)")
            if version < 2:
                db.execute("ALTER TABLE runs ADD COLUMN owner TEXT")
                db.execute("ALTER TABLE runs ADD COLUMN pending INTEGER NOT NULL DEFAULT 1")
                # Never adopt a legacy active worker into the ownership protocol.
                db.execute(
                    "UPDATE runs SET data=json_set(data,'$.state','error','$.outcome','inconclusive',"
                    "'$.finished_at',strftime('%Y-%m-%dT%H:%M:%SZ','now'),'$.effects','unknown',"
                    "'$.error',json(?)) WHERE json_extract(data,'$.state')='running'",
                    (
                        '{"code":"UPGRADE_INTERRUPTED","message":"Recover and reconcile this legacy invocation"}',
                    ),
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS savings_baselines(ref TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS runs_started_at ON runs(julianday(json_extract(data,'$.started_at')))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS measurement_tasks(id TEXT PRIMARY KEY,source_key TEXT UNIQUE NOT NULL,data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS measurement_baselines(ref TEXT NOT NULL,task_id TEXT NOT NULL,PRIMARY KEY(ref,task_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS measurement_coverage(ref TEXT PRIMARY KEY,data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS measurement_run_tasks(run_id TEXT PRIMARY KEY,task_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS measurement_emissions(id INTEGER PRIMARY KEY,data TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=4")
            db.execute(
                "CREATE TABLE IF NOT EXISTS measurement_drafts(path_key TEXT PRIMARY KEY,task_id TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(self.path, timeout=10)
            self.path.chmod(0o600)
            yield db
            db.commit()
        except sqlite3.Error as error:
            if db:
                with suppress(sqlite3.Error):
                    db.rollback()
            code = getattr(error, "sqlite_errorcode", 0)
            if code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
                raise OwlError(
                    "STORAGE_CORRUPT", "Database is unreadable; restore a backup without discarding run locks"
                ) from error
            if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise OwlError("STORAGE_BUSY", "Database is busy; retry with the same request ID") from error
            raise OwlError(
                "STORAGE_ERROR", "Database operation failed; check disk space and permissions"
            ) from error
        except BaseException:
            if db:
                with suppress(sqlite3.Error):
                    db.rollback()
            raise
        finally:
            if db:
                db.close()


class SqliteWorkflows:
    def __init__(self, db: SqliteDatabase) -> None:
        self.db = db

    def get(self, ref: str) -> Workflow:
        with self.db.connect() as db:
            row = db.execute("SELECT data FROM workflows WHERE ref=?", (ref,)).fetchone()
        if row is None:
            raise OwlError("WORKFLOW_NOT_FOUND", "Use an exact workflow reference returned by find")
        return Workflow.model_validate_json(cast(str, row[0]))

    def search(
        self,
        query: str,
        *,
        environment: str | None = None,
        repository: str | None = None,
        platform: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[Workflow]:
        words = re.findall(r"\w+", query, re.UNICODE)[:12]
        expression = " OR ".join(f'"{word}"' for word in words) if words else '""'
        with self.db.connect() as db:
            rows = db.execute(
                """WITH ranked AS (
                SELECT ref, bm25(workflow_search,0,10,3,6,2) AS score FROM workflow_search
                WHERE workflow_search MATCH ?)
                SELECT w.data FROM workflows w LEFT JOIN ranked r ON w.ref=r.ref
                WHERE w.active=1 AND (w.id=? OR w.ref=? OR r.ref IS NOT NULL)
                AND (? IS NULL OR EXISTS(SELECT 1 FROM json_each(w.data,'$.manifest.environments') WHERE value=?))
                AND (? IS NULL OR json_extract(w.data,'$.manifest.requirements.repository') IS NULL
                     OR json_extract(w.data,'$.manifest.requirements.repository')=?)
                AND (? IS NULL OR json_array_length(w.data,'$.manifest.requirements.platforms')=0
                     OR EXISTS(SELECT 1 FROM json_each(w.data,'$.manifest.requirements.platforms') WHERE value=?))
                ORDER BY CASE WHEN w.id=? OR w.ref=? THEN 0 ELSE 1 END, r.score, w.ref LIMIT ? OFFSET ?""",
                (
                    expression,
                    query,
                    query,
                    environment,
                    environment,
                    repository,
                    repository,
                    platform,
                    platform,
                    query,
                    query,
                    limit,
                    offset,
                ),
            ).fetchall()
        return [Workflow.model_validate_json(cast(str, row[0])) for row in rows]

    def replace_catalog(self, name: str, workflows: Sequence[Workflow]) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM workflow_search WHERE ref IN (SELECT ref FROM workflows WHERE catalog=?)",
                (name,),
            )
            db.execute("UPDATE workflows SET active=0 WHERE catalog=?", (name,))
            for workflow in workflows:
                db.execute(
                    "INSERT INTO workflows(ref,catalog,id,data,active) VALUES(?,?,?,?,1) "
                    "ON CONFLICT(ref) DO UPDATE SET active=1,data=excluded.data",
                    (workflow.ref, name, workflow.id, encode(workflow)),
                )
                m = workflow.manifest
                db.execute(
                    "INSERT INTO workflow_search VALUES(?,?,?,?,?)",
                    (workflow.ref, workflow.id, m.description, " ".join(m.aliases), " ".join(m.examples)),
                )

    def add_draft(self, workflow: Workflow) -> None:
        with self.db.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO workflows(ref,catalog,id,data,active) VALUES(?,?,?,?,0)",
                (workflow.ref, workflow.catalog, workflow.id, encode(workflow)),
            )
