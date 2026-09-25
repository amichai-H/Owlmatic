"""Transactional measurement ledger. Task identity and attribution cannot silently change."""

import hashlib
from pathlib import Path

from ...errors import OwlError
from ...measurement.contracts import BaselineLink, CostCoverage, Emission, TaskObservation
from ..sqlite import SqliteDatabase


class SqliteMeasurements:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def capture_draft(self, path: Path, task_id: str) -> None:
        key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO measurement_drafts VALUES (?,?) ON CONFLICT(path_key) DO UPDATE SET task_id=excluded.task_id",
                (key, task_id),
            )

    def draft_task(self, path: Path) -> str | None:
        key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
        with self.database.connect() as db:
            row = db.execute("SELECT task_id FROM measurement_drafts WHERE path_key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def put(self, observation: TaskObservation) -> bool:
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT data FROM measurement_tasks WHERE id=?", (observation.task_id,)
            ).fetchone()
            if old:
                previous = TaskObservation.model_validate_json(old[0])
                identity = ("source_key", "host", "purpose", "workload")
                if any(getattr(previous, key) != getattr(observation, key) for key in identity):
                    raise OwlError(
                        "TASK_ID_CONFLICT", "Task identity or attribution changed; use a distinct task ID"
                    )
                if previous.complete and (
                    previous.run_ids != observation.run_ids
                    or previous.workflow_ref != observation.workflow_ref
                ):
                    raise OwlError("TASK_ID_CONFLICT", "Completed task attribution is immutable")
                if not set(previous.run_ids).issubset(observation.run_ids):
                    raise OwlError("HISTORY_REGRESSED", "Previously observed run references disappeared")
                if previous.model and observation.model != previous.model:
                    raise OwlError("TASK_ID_CONFLICT", "Task model changed")
                if previous.model_dump(exclude={"observed_at"}) == observation.model_dump(
                    exclude={"observed_at"}
                ):
                    return False
                if previous.complete and not observation.complete:
                    raise OwlError(
                        "HISTORY_REGRESSED", "A complete task cannot be replaced by incomplete history"
                    )
                if previous.complete and previous.source_digest != observation.source_digest:
                    raise OwlError(
                        "HISTORY_CHANGED", "Completed task history is immutable; select explicit task ranges"
                    )
            if db.execute(
                "SELECT id FROM measurement_tasks WHERE source_key=? AND id<>?",
                (observation.source_key, observation.task_id),
            ).fetchone():
                raise OwlError("DUPLICATE_HISTORY", "This history selection already belongs to another task")
            others = db.execute("SELECT data FROM measurement_tasks WHERE id<>?", (observation.task_id,))
            for row in others:
                other = TaskObservation.model_validate_json(row[0])
                same_file = bool(observation.source_file_key) and (
                    observation.source_file_key == other.source_file_key
                )
                overlaps = (other.last_line is None or observation.first_line <= other.last_line) and (
                    observation.last_line is None or other.first_line <= observation.last_line
                )
                copied_history = (
                    observation.complete
                    and other.complete
                    and observation.host == other.host
                    and observation.source_digest == other.source_digest
                )
                if (same_file and overlaps) or copied_history:
                    raise OwlError(
                        "DUPLICATE_HISTORY", "Overlapping or copied history is already accounted for"
                    )
            for run_id in observation.run_ids:
                owner = db.execute(
                    "SELECT task_id FROM measurement_run_tasks WHERE run_id=?", (run_id,)
                ).fetchone()
                if owner and owner[0] != observation.task_id:
                    raise OwlError(
                        "RUN_ALREADY_ATTRIBUTED", "A workflow run can belong to only one measured task"
                    )
                db.execute(
                    "INSERT OR IGNORE INTO measurement_run_tasks VALUES (?,?)", (run_id, observation.task_id)
                )
            db.execute(
                "INSERT INTO measurement_tasks VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (observation.task_id, observation.source_key, observation.model_dump_json()),
            )
            return True

    def tasks(self) -> tuple[TaskObservation, ...]:
        with self.database.connect() as db:
            return tuple(
                TaskObservation.model_validate_json(row[0])
                for row in db.execute("SELECT data FROM measurement_tasks ORDER BY id")
            )

    def link(self, link: BaselineLink) -> None:
        with self.database.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO measurement_baselines VALUES (?,?)", (link.workflow_ref, link.task_id)
            )

    def links(self, ref: str) -> tuple[BaselineLink, ...]:
        with self.database.connect() as db:
            return tuple(
                BaselineLink(workflow_ref=ref, task_id=row[0])
                for row in db.execute(
                    "SELECT task_id FROM measurement_baselines WHERE ref=? ORDER BY task_id", (ref,)
                )
            )

    def coverage(self, ref: str) -> CostCoverage:
        with self.database.connect() as db:
            row = db.execute("SELECT data FROM measurement_coverage WHERE ref=?", (ref,)).fetchone()
        return CostCoverage.model_validate_json(row[0]) if row else CostCoverage(workflow_ref=ref)

    def declare(self, coverage: CostCoverage) -> None:
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO measurement_coverage VALUES (?,?) ON CONFLICT(ref) DO UPDATE SET data=excluded.data",
                (coverage.workflow_ref, coverage.model_dump_json()),
            )

    def emit(self, emission: Emission) -> None:
        with self.database.connect() as db:
            db.execute("INSERT INTO measurement_emissions(data) VALUES (?)", (emission.model_dump_json(),))

    def retain_since(self, timestamp: str) -> None:
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT id,json_extract(data,'$.workflow_ref') FROM measurement_tasks WHERE json_extract(data,'$.observed_at')<?",
                (timestamp,),
            ).fetchall()
            for task_id, ref in old:
                db.execute("DELETE FROM measurement_baselines WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM measurement_drafts WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM measurement_run_tasks WHERE task_id=?", (task_id,))
                db.execute("DELETE FROM measurement_tasks WHERE id=?", (task_id,))
                db.execute("DELETE FROM measurement_coverage WHERE ref=?", (ref,))
            db.execute(
                "DELETE FROM measurement_emissions WHERE json_extract(data,'$.emitted_at')<?", (timestamp,)
            )
