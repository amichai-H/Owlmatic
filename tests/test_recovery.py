from dataclasses import replace
from pathlib import Path

import pytest

from owlmatic.bootstrap import create_application
from owlmatic.domain import FinalizationReport, Job, Profile, Run, Settings, Target, Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.files import FileArtifacts
from owlmatic.infrastructure.run_repository import SqliteRuns
from owlmatic.infrastructure.sqlite import SqliteDatabase
from owlmatic.recovery_service import RecoveryService
from tests.fakes import FakeClock, FakeHost, MemorySettings
from tests.test_supervisor import SimulatedProcess


class FailingResult(FileArtifacts):
    def save_result(self, run: Run) -> None:
        raise OSError("simulated full disk")


class FailingCleanup(FileArtifacts):
    def scrub_inputs(self, run_id: str) -> None:
        raise OSError("simulated permission error")


@pytest.mark.parametrize("artifacts_type", [FailingResult, FailingCleanup])
def test_finalization_retries_after_restart(
    tmp_path: Path, workflow: Workflow, artifacts_type: type[FileArtifacts]
) -> None:
    app = create_application(tmp_path)
    artifacts = artifacts_type(tmp_path)
    run = Run(
        run_id="run_" + "c" * 32,
        workflow_ref=workflow.ref,
        target=Target(environment="simulation", workspace=str(tmp_path)),
        started_at="fixed",
        evidence_ref="fixture",
    )
    job = Job(workflow=workflow, run=run, profile="default", inputs={"healthy": True})
    app.execution.runs.create(run, "request", "fingerprint", "resource")
    artifacts.prepare(job)
    settings = MemorySettings(Settings(profiles={"default": Profile(grants=(workflow.ref,))}))
    recovery = RecoveryService(app.execution.runs, artifacts, app.supervisor.guard)
    supervisor = replace(
        app.supervisor,
        artifacts=artifacts,
        process=SimulatedProcess(),
        recovery=recovery,
        policy=replace(app.supervisor.policy, settings=settings, host=FakeHost()),
    )
    terminal = supervisor.execute(job)
    assert terminal.outcome == "pass"
    assert app.execution.runs.get(run.run_id).outcome == "pass"
    assert app.execution.runs.is_pending(run.run_id)
    assert app.execution.runs.expired(float("inf")) == []
    if artifacts_type is FailingResult:
        assert not (artifacts.directory(run.run_id) / "inputs.json").exists()
    else:
        assert (artifacts.directory(run.run_id) / "result.json").read_text().find('"completed"') >= 0
    with pytest.raises(OwlError) as caught:
        recovery.reconcile(run.run_id)
    assert caught.value.code == "RECOVERY_REQUIRED"
    restarted = create_application(tmp_path)
    report = restarted.recovery.recover()
    assert len(report.runs) == 1 and not report.runs[0].pending
    assert not restarted.execution.runs.is_pending(run.run_id)
    assert not (artifacts.directory(run.run_id) / "inputs.json").exists()
    assert not (artifacts.directory(run.run_id) / "job.json").exists()
    assert restarted.recovery.recover().runs == ()


def test_reconciliation_refuses_live_process_even_after_expiry(tmp_path: Path) -> None:
    app = create_application(tmp_path)
    clock = FakeClock()
    runs = SqliteRuns(SqliteDatabase(tmp_path), clock)
    recovery = RecoveryService(runs, app.execution.artifacts, app.supervisor.guard)
    run = Run(
        run_id="run_" + "d" * 32,
        workflow_ref="fixture",
        effects="unknown",
        target=Target(environment="simulation", workspace=str(tmp_path)),
        started_at="fixed",
        evidence_ref="fixture",
    )
    runs.create(run, None, "fingerprint", "resource")
    runs.claim(run.run_id, "owner")
    with app.supervisor.guard.hold(run.run_id):
        clock.sleep(31)
        assert runs.get(run.run_id).state == "error"
        report = recovery.finalize(run.run_id)
        assert report.active and report.pending
        with pytest.raises(OwlError) as caught:
            recovery.reconcile(run.run_id)
        assert caught.value.code == "RECOVERY_REQUIRED"
    recovery.reconcile(run.run_id)
    assert not runs.is_pending(run.run_id)
    assert runs.get(run.run_id).effects == "unknown"


class SimulatedCrash(BaseException):
    pass


class CrashingRecovery(RecoveryService):
    def finalize(self, run_id: str) -> FinalizationReport:
        raise SimulatedCrash("process stopped after terminal commit")


def test_crash_after_commit_is_recovered_without_execution(tmp_path: Path, workflow: Workflow) -> None:
    app = create_application(tmp_path)
    run = Run(
        run_id="run_" + "1" * 32,
        workflow_ref=workflow.ref,
        target=Target(environment="simulation", workspace=str(tmp_path)),
        started_at="fixed",
        evidence_ref="fixture",
    )
    job = Job(workflow=workflow, run=run, profile="default", inputs={"healthy": True})
    app.execution.runs.create(run, None, "fingerprint", "resource")
    app.execution.artifacts.prepare(job)
    settings = MemorySettings(Settings(profiles={"default": Profile(grants=(workflow.ref,))}))
    supervisor = replace(
        app.supervisor,
        process=SimulatedProcess(),
        policy=replace(app.supervisor.policy, settings=settings, host=FakeHost()),
        recovery=CrashingRecovery(app.execution.runs, app.execution.artifacts, app.supervisor.guard),
    )
    with pytest.raises(SimulatedCrash):
        supervisor.execute(job)
    assert app.execution.runs.get(run.run_id).outcome == "pass"
    assert app.execution.runs.is_pending(run.run_id)
    restarted = create_application(tmp_path)
    assert not restarted.recovery.recover().runs[0].pending
    assert restarted.execution.runs.get(run.run_id).outcome == "pass"


class FailedCommitRuns(SqliteRuns):
    def finish(self, run: Run, owner: str | None) -> None:
        raise OwlError("STORAGE_ERROR", "simulated commit failure")


def test_failed_terminal_commit_keeps_lock_and_recovers_as_unknown(
    tmp_path: Path, workflow: Workflow
) -> None:
    app = create_application(tmp_path)
    clock = FakeClock()
    runs = FailedCommitRuns(SqliteDatabase(tmp_path), clock)
    run = Run(
        run_id="run_" + "2" * 32,
        workflow_ref=workflow.ref,
        effects="unknown",
        target=Target(environment="simulation", workspace=str(tmp_path)),
        started_at="fixed",
        evidence_ref="fixture",
    )
    job = Job(workflow=workflow, run=run, profile="default", inputs={"healthy": True})
    runs.create(run, None, "fingerprint", "resource")
    app.execution.artifacts.prepare(job)
    settings = MemorySettings(Settings(profiles={"default": Profile(grants=(workflow.ref,))}))
    supervisor = replace(
        app.supervisor,
        runs=runs,
        process=SimulatedProcess(),
        policy=replace(app.supervisor.policy, settings=settings, host=FakeHost()),
    )
    with pytest.raises(OwlError) as caught:
        supervisor.execute(job)
    assert caught.value.code == "STORAGE_ERROR"
    assert runs.get(run.run_id).state == "running"
    clock.sleep(31)
    restored = SqliteRuns(SqliteDatabase(tmp_path), clock)
    recovery = RecoveryService(restored, app.execution.artifacts, app.supervisor.guard)
    report = recovery.recover()
    assert len(report.runs) == 1 and not report.runs[0].pending
    assert restored.get(run.run_id).effects == "unknown"
    with pytest.raises(OwlError) as caught:
        restored.create(
            Run(
                run_id="run_" + "3" * 32,
                workflow_ref="fixture",
                target=run.target,
                started_at="fixed",
                evidence_ref="fixture",
            ),
            None,
            "new",
            "resource",
        )
    assert caught.value.code == "BUSY"
    assert not (app.execution.artifacts.directory(run.run_id) / "inputs.json").exists()
