"""Transactional run ownership, lease expiry, and recoverable finalization."""

from collections.abc import Sequence
from typing import cast

from ..domain import Failure, Run
from ..errors import OwlError
from ..ports import Clock
from ..serialization import encode
from .sqlite import SqliteDatabase


class SqliteRuns:
    def __init__(self, db: SqliteDatabase, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def create(self, run: Run, request_id: str | None, fingerprint: str, lock_key: str | None) -> Run:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_id:
                row = db.execute(
                    "SELECT data,fingerprint FROM runs WHERE request_id=?", (request_id,)
                ).fetchone()
                if row:
                    if row[1] != fingerprint:
                        raise OwlError(
                            "REQUEST_CONFLICT", "Request ID was already used with a different invocation"
                        )
                    return Run.model_validate_json(cast(str, row[0]))
            if lock_key:
                if db.execute("SELECT 1 FROM locks WHERE key=?", (lock_key,)).fetchone():
                    raise OwlError(
                        "BUSY", "Another invocation owns this resource; reconcile it before retrying"
                    )
                db.execute("INSERT INTO locks(key,run_id) VALUES(?,?)", (lock_key, run.run_id))
            db.execute(
                "INSERT INTO runs(id,data,heartbeat,request_id,fingerprint,pending) VALUES(?,?,?,?,?,0)",
                (run.run_id, encode(run), self.clock.epoch(), request_id, fingerprint),
            )
        return run

    def get(self, run_id: str) -> Run:
        with self.db.connect() as db:
            # Serialize expiry with heartbeats and completion. A SELECT followed by
            # an unconditional UPDATE could overwrite an already committed result.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,heartbeat FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OwlError("RUN_NOT_FOUND", "Unknown run ID")
            run = Run.model_validate_json(cast(str, row[0]))
            if run.state == "running" and self.clock.epoch() - cast(float, row[1]) > 30:
                run = run.finish(
                    state="error",
                    outcome="inconclusive",
                    effects=run.effects,
                    finished_at=self.clock.iso(),
                    error=Failure(
                        code="WORKER_LOST",
                        message="Worker heartbeat lost; recover local execution before reconciling external effects",
                    ),
                )
                db.execute("UPDATE runs SET data=?,owner=NULL,pending=1 WHERE id=?", (encode(run), run_id))
        return run

    def claim(self, run_id: str, owner: str) -> Run:
        # Check expiry first. The following conditional UPDATE remains authoritative
        # if another process expires, claims, or finishes this run in between.
        self.get(run_id)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE runs SET owner=?,heartbeat=? WHERE id=? AND owner IS NULL "
                "AND json_extract(data,'$.state')='running' AND heartbeat>=?",
                (owner, self.clock.epoch(), run_id, self.clock.epoch() - 30),
            ).rowcount
            if changed != 1:
                raise OwlError("LEASE_LOST", "This worker does not own the active invocation")
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            return Run.model_validate_json(cast(str, row[0]))

    def finish(self, run: Run, owner: str | None) -> None:
        if run.state == "running":
            raise OwlError("INVALID_TRANSITION", "Finalization requires a terminal run")
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id=?", (run.run_id,)).fetchone()
            if row is None:
                raise OwlError("RUN_NOT_FOUND", "Unknown run ID")
            original = Run.model_validate_json(cast(str, row[0]))
            if (run.workflow_ref, run.profile, run.purpose, run.target, run.started_at, run.evidence_ref) != (
                original.workflow_ref,
                original.profile,
                original.purpose,
                original.target,
                original.started_at,
                original.evidence_ref,
            ):
                raise OwlError("INVALID_TRANSITION", "Invocation identity cannot change during completion")
            changed = db.execute(
                "UPDATE runs SET data=?,heartbeat=?,owner=NULL,pending=1 WHERE id=? "
                "AND owner IS ? AND json_extract(data,'$.state')='running' AND heartbeat>=?",
                (encode(run), self.clock.epoch(), run.run_id, owner, self.clock.epoch() - 30),
            ).rowcount
            if changed != 1:
                raise OwlError("LEASE_LOST", "A stale worker cannot replace the invocation result")

    def heartbeat(self, run_id: str, owner: str) -> None:
        with self.db.connect() as db:
            changed = db.execute(
                "UPDATE runs SET heartbeat=? WHERE id=? AND owner=? "
                "AND json_extract(data,'$.state')='running' AND heartbeat>=?",
                (self.clock.epoch(), run_id, owner, self.clock.epoch() - 30),
            ).rowcount
            if changed != 1:
                raise OwlError("LEASE_LOST", "Execution ownership was revoked; stop this process")

    def set_pid(self, run_id: str, pid: int) -> None:
        with self.db.connect() as db:
            db.execute("UPDATE runs SET pid=? WHERE id=?", (pid, run_id))

    def settled(self, run_id: str, release_lock: bool) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OwlError("RUN_NOT_FOUND", "Unknown run ID")
            run = Run.model_validate_json(cast(str, row[0]))
            if run.state == "running":
                raise OwlError("RUN_ACTIVE", "Active invocations cannot be settled")
            if release_lock:
                db.execute("DELETE FROM locks WHERE run_id=?", (run_id,))
            db.execute("UPDATE runs SET pending=0 WHERE id=?", (run_id,))

    def release_lock(self, run_id: str) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,pending FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OwlError("RUN_NOT_FOUND", "Unknown run ID")
            if Run.model_validate_json(cast(str, row[0])).state == "running" or row[1]:
                raise OwlError("RECOVERY_REQUIRED", "Finish local recovery before releasing this resource")
            db.execute("DELETE FROM locks WHERE run_id=?", (run_id,))

    def is_pending(self, run_id: str) -> bool:
        with self.db.connect() as db:
            row = db.execute("SELECT pending FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OwlError("RUN_NOT_FOUND", "Unknown run ID")
            return bool(row[0])

    def pending(self, limit: int = 100) -> Sequence[str]:
        with self.db.connect() as db:
            return [
                cast(str, row[0])
                for row in db.execute(
                    "SELECT id FROM runs WHERE pending=1 OR "
                    "(heartbeat<? AND json_extract(data,'$.state')='running') ORDER BY heartbeat,id LIMIT ?",
                    (self.clock.epoch() - 30, limit),
                )
            ]

    def expired(self, cutoff: float) -> Sequence[Run]:
        with self.db.connect() as db:
            rows = db.execute(
                "SELECT data FROM runs WHERE heartbeat<? AND pending=0 "
                "AND json_extract(data,'$.state')!='running' AND id NOT IN (SELECT run_id FROM locks)",
                (cutoff,),
            ).fetchall()
        return [Run.model_validate_json(cast(str, row[0])) for row in rows]

    def delete(self, run_id: str) -> None:
        with self.db.connect() as db:
            db.execute(
                "DELETE FROM runs WHERE id=? AND pending=0 AND json_extract(data,'$.state')!='running' "
                "AND id NOT IN (SELECT run_id FROM locks)",
                (run_id,),
            )
