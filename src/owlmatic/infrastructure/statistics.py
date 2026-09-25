"""Aggregate safe ledger fields in SQLite; never retrieve workflow inputs or logs."""

from datetime import date
from typing import cast

from ..domain import Effects, Outcome, State
from ..serialization import encode
from ..statistics import RunBucket, RunPurpose, SavingsBaseline
from .sqlite import SqliteDatabase


class SqliteStatistics:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def buckets(self, since: str, until: str) -> tuple[RunBucket, ...]:
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT date(json_extract(data,'$.started_at')) AS day,
                json_extract(data,'$.workflow_ref') AS ref,
                coalesce(json_extract(data,'$.purpose'),'unknown') AS purpose,
                json_extract(data,'$.state') AS state,
                json_extract(data,'$.outcome') AS outcome,
                json_extract(data,'$.effects') AS effects,
                count(*), sum(coalesce(json_extract(data,'$.duration_ms'),0))
                FROM runs WHERE julianday(json_extract(data,'$.started_at')) >= julianday(?)
                AND julianday(json_extract(data,'$.started_at')) <= julianday(?)
                GROUP BY day, ref, purpose, state, outcome, effects
                ORDER BY day, ref, purpose, state, outcome, effects""",
                (since, until),
            ).fetchall()
        return tuple(
            RunBucket(
                day=date.fromisoformat(cast(str, row[0])),
                ref=cast(str, row[1]),
                purpose=cast(RunPurpose, row[2]),
                state=cast(State, row[3]),
                outcome=cast(Outcome, row[4]),
                effects=cast(Effects, row[5]),
                count=cast(int, row[6]),
                duration_ms=cast(int, row[7]),
            )
            for row in rows
        )

    def baselines(self) -> tuple[SavingsBaseline, ...]:
        with self.database.connect() as db:
            rows = db.execute("SELECT data FROM savings_baselines ORDER BY ref").fetchall()
        return tuple(SavingsBaseline.model_validate_json(cast(str, row[0])) for row in rows)

    def save_baseline(self, baseline: SavingsBaseline) -> None:
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO savings_baselines(ref,data) VALUES(?,?) "
                "ON CONFLICT(ref) DO UPDATE SET data=excluded.data",
                (baseline.ref, encode(baseline)),
            )
