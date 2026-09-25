from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from owlmatic.bootstrap import create_application
from owlmatic.domain import Check, Evidence, Job, Profile, Run, Settings, Target, Workflow
from owlmatic.errors import OwlError
from owlmatic.ports import ProcessObserver, ProcessRequest
from owlmatic.serialization import atomic_model
from tests.fakes import FakeClock, FakeHost, MemorySettings


@dataclass
class SimulatedProcess:
    error_code: str | None = None
    executed: bool = False

    def execute(self, request: ProcessRequest, observer: ProcessObserver) -> int:
        self.executed = True
        observer.output("stdout", b"fixture output", final=True)
        observer.heartbeat()
        if self.error_code:
            raise OwlError(self.error_code, "Simulated interruption")
        evidence = Evidence(
            run_id=request.environment["OWLMATIC_RUN_ID"],
            outcome="pass",
            checks=(Check(name="suite_passed", status="pass"),),
            outputs={"tests": 2, "suite_passed": True},
            effects="none",
        )
        atomic_model(Path(request.environment["OWLMATIC_RESULT"]), evidence)
        return 0


@pytest.mark.parametrize(
    "error,state", [(None, "completed"), ("TIMEOUT", "error"), ("CANCELLED", "cancelled")]
)
def test_lifecycle_with_injected_process_and_clock(
    tmp_path: Path, workflow: Workflow, error: str | None, state: str
) -> None:
    app = create_application(tmp_path)
    clock = FakeClock()
    run = Run(
        run_id="run_" + "a" * 32,
        workflow_ref=workflow.ref,
        target=Target(environment="simulation", workspace=str(tmp_path)),
        started_at=clock.iso(),
        evidence_ref="fixture",
    )
    job = Job(workflow=workflow, run=run, profile="default", inputs={"healthy": True})
    app.execution.runs.create(run, None, "fingerprint", None)
    app.execution.artifacts.prepare(job)
    settings = MemorySettings(Settings(profiles={"default": Profile(grants=(workflow.ref,))}))
    policy = replace(app.execution.policy, settings=settings, host=FakeHost())
    process = SimulatedProcess(error)
    supervisor = replace(app.supervisor, policy=policy, process=process, clock=clock)
    result = supervisor.execute(job)
    assert process.executed
    assert result.state == state
    assert result.duration_ms == 0
    assert result.outcome == ("pass" if error is None else "inconclusive")
    assert not (tmp_path / "runs" / run.run_id / "inputs.json").exists()


def test_revocation_between_submission_and_execution_prevents_launch(
    tmp_path: Path, workflow: Workflow
) -> None:
    app = create_application(tmp_path)
    run = Run(
        run_id="run_" + "b" * 32,
        workflow_ref=workflow.ref,
        target=Target(environment="simulation", workspace=str(tmp_path)),
        started_at="fixed",
        evidence_ref="fixture",
    )
    job = Job(workflow=workflow, run=run, profile="default", inputs={"healthy": True})
    app.execution.runs.create(run, None, "fingerprint", None)
    app.execution.artifacts.prepare(job)
    process = SimulatedProcess()
    supervisor = replace(app.supervisor, process=process, clock=FakeClock())
    result = supervisor.execute(job)
    assert not process.executed
    assert result.error and result.error.code == "UNTRUSTED"
