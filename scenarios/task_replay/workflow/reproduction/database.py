"""Read-only extraction of a bounded, point-in-time fixture snapshot; no arbitrary SQL."""

import sqlite3
from contextlib import closing
from pathlib import Path

from .contracts import AccountState, Environment, Incident, JobInput, LineItem, Signature, SnapshotUnavailable


class SqliteIncidents:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, execution_id: str) -> Incident:
        try:
            if not self.path.is_file() or self.path.stat().st_size > 5_000_000:
                raise SnapshotUnavailable("missing_or_oversized_fixture_database")
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("BEGIN")
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise SnapshotUnavailable("unsupported_database_schema")
                job = db.execute(
                    "SELECT task,account,expected_revision,item_count,worker_digest,runtime,configuration,stage,exception,code,replay_effects FROM failed_jobs WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()
                state = db.execute(
                    "SELECT revision,balance_cents FROM account_snapshots WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()
                items = db.execute(
                    "SELECT item_id,amount_cents FROM input_items WHERE execution_id=? ORDER BY ordinal LIMIT 101",
                    (execution_id,),
                ).fetchall()
            if job is None or state is None or not items or len(items) != job[3]:
                raise SnapshotUnavailable("incomplete_historical_snapshot")
            # SQLite rows are the dynamic boundary; every field is validated before entering the service.
            return Incident.model_validate(
                {
                    "task": job[0],
                    "original_execution": execution_id,
                    "inputs": JobInput(
                        account=job[1],
                        expected_revision=job[2],
                        items=tuple(LineItem(item_id=row[0], amount_cents=row[1]) for row in items),
                    ),
                    "initial_state": AccountState(revision=state[0], balance_cents=state[1]),
                    "environment": Environment(worker_digest=job[4], runtime=job[5], configuration=job[6]),
                    "failure": Signature(stage=job[7], exception=job[8], code=job[9]),
                    "replay_effects": job[10],
                }
            )
        except (sqlite3.Error, OSError, ValueError) as error:
            raise SnapshotUnavailable("invalid_or_unavailable_snapshot") from error
