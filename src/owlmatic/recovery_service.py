"""Idempotent projection/cleanup after a terminal result; SQLite is authoritative."""

from dataclasses import dataclass

from .domain import Failure, FinalizationReport, RecoveryReport
from .errors import OwlError
from .ports import ArtifactStore, RunGuard, RunRepository


@dataclass(frozen=True)
class RecoveryService:
    runs: RunRepository
    artifacts: ArtifactStore
    guard: RunGuard

    def finalize(self, run_id: str) -> FinalizationReport:
        with self.guard.exclusive(run_id) as idle:
            if not idle:
                return FinalizationReport(run_id=run_id, pending=True, active=True)
            run = self.runs.get(run_id)
            if run.state == "running":
                return FinalizationReport(run_id=run_id, pending=True)
            if not self.runs.is_pending(run_id):
                return FinalizationReport(run_id=run_id, pending=False)
            errors: list[Failure] = []
            # Each independent operation is attempted even if another fails. The
            # durable pending flag remains set until every operation succeeds.
            try:
                self.artifacts.save_result(run)
            except (OSError, OwlError):
                errors.append(Failure(code="RESULT_PENDING", message="Result artifact needs repair"))
            try:
                self.artifacts.scrub_inputs(run_id)
            except (OSError, OwlError):
                errors.append(Failure(code="CLEANUP_PENDING", message="Submitted input cleanup needs retry"))
            if not errors:
                self.runs.settled(run_id, release_lock=run.effects not in {"partial", "unknown"})
            return FinalizationReport(run_id=run_id, pending=bool(errors), errors=tuple(errors))

    def recover(self) -> RecoveryReport:
        reports: list[FinalizationReport] = []
        for run_id in self.runs.pending():
            # Expire first, fencing a worker even when its activity lock is held.
            self.runs.get(run_id)
            reports.append(self.finalize(run_id))
        return RecoveryReport(runs=tuple(reports))

    def reconcile(self, run_id: str) -> None:
        report = self.finalize(run_id)
        if report.pending:
            raise OwlError("RECOVERY_REQUIRED", "Execution is active or local finalization is incomplete")
        with self.guard.exclusive(run_id) as idle:
            if not idle:
                raise OwlError("RUN_ACTIVE", "A local execution process still holds this invocation")
            self.runs.release_lock(run_id)
