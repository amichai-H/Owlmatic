"""Application orchestration shared by both interfaces. All effects go through ports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from .domain import (
    Cancellation,
    Failure,
    FailureReport,
    InspectRequest,
    Job,
    Page,
    Run,
    RunRequest,
    RunSummary,
)
from .errors import OwlError
from .export_ports import CompletionObserver
from .messages import CleanupReport, ReconcileReport
from .policy import ExecutionPolicy
from .ports import ArtifactStore, Clock, RunRepository, WorkerLauncher, WorkflowRepository
from .recovery_service import RecoveryService
from .serialization import encode


def summarize(run: Run, finalization_pending: bool = False) -> RunSummary:
    result = RunSummary(run=run, finalization_pending=finalization_pending)
    if len(encode(result).encode()) > 2048:
        result = RunSummary(
            run=run.summarized(omit_outputs=True),
            outputs_omitted=True,
            finalization_pending=finalization_pending,
        )
    if len(encode(result).encode()) > 2048:
        result = RunSummary(
            run=run.summarized(omit_outputs=True, omit_workspace=True),
            outputs_omitted=True,
            finalization_pending=finalization_pending,
        )
    return result


@dataclass(frozen=True)
class ExecutionService:
    workflows: WorkflowRepository
    runs: RunRepository
    artifacts: ArtifactStore
    launcher: WorkerLauncher
    clock: Clock
    policy: ExecutionPolicy
    new_id: Callable[[], str]
    recovery: RecoveryService
    completions: CompletionObserver | None = None

    def run(self, request: RunRequest, *, validation: bool = False) -> RunSummary:
        workflow = self.workflows.get(request.ref)
        m = workflow.manifest
        environment = request.environment or (m.environments[0] if len(m.environments) == 1 else "local")
        selected = self.policy.check(
            workflow, request.inputs, request.profile, environment, request.workspace, validation=validation
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json", exclude={"request_id", "wait_seconds"}),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        lock_key = None
        if m.execution.concurrency_input:
            if m.execution.concurrency_input not in request.inputs:
                raise OwlError("MISSING_CONCURRENCY_INPUT", "The workflow's concurrency input is required")
            lock_key = hashlib.sha256(
                json.dumps(
                    [
                        workflow.catalog,
                        workflow.id,
                        environment,
                        request.inputs[m.execution.concurrency_input],
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        run_id = self.new_id()
        run = Run(
            run_id=run_id,
            workflow_ref=workflow.ref,
            profile=request.profile,
            purpose="validation" if validation else "workflow",
            effects="unknown" if m.effects else "none",
            target=self.policy.host.target(request.workspace, environment),
            started_at=self.clock.iso(),
            evidence_ref=run_id,
        )
        existing = self.runs.create(run, request.request_id, fingerprint, lock_key)
        if existing.run_id != run_id:
            return self._summary(self.wait(existing.run_id, request.wait_seconds))
        job = Job(
            workflow=workflow, run=run, profile=request.profile, inputs=request.inputs, validation=validation
        )
        try:
            self.artifacts.prepare(job)
            ambient = self.policy.host.environment
            worker_env = {"PATH": ambient.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
            for name in (*selected.env, *selected.credentials.values()):
                if name in ambient and not name.startswith("OWLMATIC_"):
                    worker_env[name] = ambient[name]
            pid = self.launcher.start(run_id, worker_env)
        except Exception as error:
            failed = run.finish(
                state="error",
                outcome="inconclusive",
                effects="none",
                finished_at=self.clock.iso(),
                error=Failure(code="START_FAILED", message="Could not start the execution worker"),
            )
            self.runs.finish(failed, None)
            # An artifact failure must not leave submitted inputs behind.
            self.recovery.finalize(run_id)
            raise OwlError("START_FAILED", "Could not start the execution worker") from error
        # The worker now owns the lifecycle. A metadata write failure must never
        # pretend that its effects were rolled back or remove its inputs.
        self.runs.set_pid(run_id, pid)
        if self.completions is not None and not validation:
            self.completions.observe(run_id, m.execution.timeout_seconds)
        return self._summary(self.wait(run_id, request.wait_seconds))

    def _summary(self, run: Run) -> RunSummary:
        return summarize(run, finalization_pending=self.runs.is_pending(run.run_id))

    def wait(self, run_id: str, seconds: float) -> Run:
        deadline = self.clock.monotonic() + min(20.0, max(0.0, seconds))
        while True:
            run = self.runs.get(run_id)
            if run.state != "running":
                self.recovery.finalize(run_id)
                return run
            if self.clock.monotonic() >= deadline:
                return run
            self.clock.sleep(0.1)

    def inspect(self, request: InspectRequest) -> RunSummary | FailureReport | Page:
        run = self.wait(request.run_id, request.wait_seconds)
        if request.view == "status":
            return self._summary(run)
        if request.view == "failures":
            evidence = self.artifacts.evidence(request.run_id)
            failures = tuple(c for c in evidence.checks if c.status != "pass") if evidence else ()
            return FailureReport(
                run_id=run.run_id,
                state=run.state,
                outcome=run.outcome,
                error=run.error,
                checks=failures[:8],
                more=len(failures) > 8,
            )
        return self.artifacts.page(request)

    def cancel(self, run_id: str) -> Cancellation:
        run = self.runs.get(run_id)
        if run.state == "running":
            self.artifacts.request_cancel(run_id)
        return Cancellation(run_id=run_id, cancellation_requested=run.state == "running", effects=run.effects)

    def reconcile(self, run_id: str) -> ReconcileReport:
        self.recovery.reconcile(run_id)
        return ReconcileReport(run_id=run_id, lock_released=True)

    def cleanup(self, days: int | None = None) -> CleanupReport:
        days = days if days is not None else self.policy.settings.load().retention_days
        if days < 0:
            raise OwlError("INVALID_RETENTION", "Retention days must be nonnegative")
        self.recovery.recover()
        expired = self.runs.expired(self.clock.epoch() - days * 86400)
        removed = 0
        for run in expired:
            with self.recovery.guard.exclusive(run.run_id) as idle:
                if idle:
                    self.artifacts.remove(run.run_id)
                    self.runs.delete(run.run_id)
                    removed += 1
        return CleanupReport(removed_runs=removed, retention_days=days)
