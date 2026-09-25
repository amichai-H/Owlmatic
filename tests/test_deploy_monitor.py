"""Safety assertions for the lab, including negative controls that fool naive monitoring."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from scenarios.deploy_monitor.fixtures import Fault, Plan, Record, log_page, status
from scenarios.deploy_monitor.store import Store
from scenarios.deploy_monitor.workflow.monitoring.contracts import (
    DeploymentStatus,
    DeployRequest,
    Inputs,
    LogPage,
    LogQuery,
    Receipt,
    Unavailable,
)
from scenarios.deploy_monitor.workflow.monitoring.http import local_url
from scenarios.deploy_monitor.workflow.monitoring.service import Monitor


@dataclass
class FakeClock:
    now: float = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.now += seconds


class Backend:
    def __init__(self, clock: FakeClock, plan: Plan, corruption: str = "") -> None:
        self.clock = clock
        self.plan = plan
        self.corruption = corruption
        self.record: Record | None = None
        self.deploy_calls = 0

    def deploy(self, request: DeployRequest) -> Receipt:
        self.deploy_calls += 1
        if self.corruption == "lost_ack":
            raise Unavailable("ack lost after deployment")
        receipt = Receipt(
            deployment_id="deploy-1",
            service=request.service,
            version=request.version,
            replicas=("api-0", "api-1", "api-2"),
        )
        self.record = Record(receipt=receipt, request=request, accepted_at=self.clock.now, plan=self.plan)
        return receipt

    def status(self, deployment_id: str) -> DeploymentStatus:
        assert self.record is not None
        value = status(self.record, self.clock.now)
        data = value.model_dump()
        if self.corruption == "status_identity":
            data["deployment_id"] = "another-deployment"
        if self.corruption == "clock_jump" and self.clock.now > 1003:
            data["observed_at"] = self.clock.now + 60
        return DeploymentStatus.model_validate(data)

    def read(self, query: LogQuery) -> LogPage:
        assert self.record is not None
        if self.plan.fault in {"unavailable", "malformed"}:
            raise Unavailable("upstream unavailable or invalid")
        page = log_page(self.record, query, self.clock.now)
        data = page.model_dump()
        if self.corruption == "identity":
            data["deployment_id"] = "another-deployment"
        if self.corruption == "missing_heartbeat":
            data["events"] = tuple(e.model_dump() for e in page.events if e.replica != "api-2")
        if self.corruption == "duplicate" and len(page.events) > 1:
            data["events"] = (page.events[0].model_dump(), page.events[0].model_dump())
        if self.corruption == "event_version":
            data["events"] = tuple({**e.model_dump(), "version": "old"} for e in page.events)
        if self.corruption == "other_replica":
            data["events"] = tuple({**e.model_dump(), "replica": "other"} for e in page.events)
        if self.corruption == "snapshot_changed" and query.cursor:
            data["complete_through"] = page.complete_through + 1
        return LogPage.model_validate(data)


def inputs(**changes: object) -> Inputs:
    return Inputs.model_validate(
        {
            "deploy_url": "http://127.0.0.1:9876",
            "logs_url": "http://127.0.0.1:9877",
            "service": "api",
            "version": "v2",
            "request_key": "test",
            "monitor_minutes": 1.0,
            "ingestion_grace_seconds": 5.0,
            **changes,
        }
    )


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("healthy", "pass"),
        ("wrong_version", "fail"),
        ("missing_replica", "fail"),
        ("late_error", "fail"),
        ("boundary_error", "fail"),
        ("drift", "fail"),
        ("missing_logs", "inconclusive"),
        ("stale_logs", "inconclusive"),
        ("gap", "inconclusive"),
        ("cursor_loop", "inconclusive"),
        ("unavailable", "inconclusive"),
        ("malformed", "inconclusive"),
    ],
)
def test_fault_matrix(fault: Fault, expected: str) -> None:
    clock = FakeClock()
    backend = Backend(
        clock, Plan(fault=fault, error_after_seconds=60.0 if fault == "boundary_error" else 30.0)
    )
    result = Monitor(backend, backend, clock).run(inputs())
    assert result.outcome == expected
    assert backend.deploy_calls == 1
    if expected == "pass":
        assert clock.now >= 1061
        assert result.observed_seconds == 60
        assert result.events_checked >= 180  # exceeds a single log page


@pytest.mark.parametrize(
    "corruption",
    [
        "identity",
        "status_identity",
        "missing_heartbeat",
        "duplicate",
        "other_replica",
        "snapshot_changed",
        "clock_jump",
        "lost_ack",
    ],
)
def test_corrupt_evidence_never_passes(corruption: str) -> None:
    clock = FakeClock()
    backend = Backend(clock, Plan(), corruption)
    result = Monitor(backend, backend, clock).run(inputs())
    assert result.outcome == "inconclusive"
    assert backend.deploy_calls == 1
    if corruption == "lost_ack":
        assert result.effects == "unknown"


def test_log_history_version_change_fails() -> None:
    clock = FakeClock()
    backend = Backend(clock, Plan(), "event_version")
    assert Monitor(backend, backend, clock).run(inputs()).outcome == "fail"


@pytest.mark.parametrize(("fault", "expected"), [("healthy", "pass"), ("boundary_error", "fail")])
def test_waits_for_delayed_boundary_logs(fault: Fault, expected: str) -> None:
    clock = FakeClock()
    backend = Backend(clock, Plan(fault=fault, error_after_seconds=60.0, ingestion_delay_seconds=3.0))
    result = Monitor(backend, backend, clock).run(inputs())
    assert result.outcome == expected
    assert clock.now >= 1064


def test_ingestion_later_than_grace_is_inconclusive() -> None:
    clock = FakeClock()
    backend = Backend(
        clock, Plan(fault="boundary_error", error_after_seconds=60.0, ingestion_delay_seconds=10.0)
    )
    assert Monitor(backend, backend, clock).run(inputs()).outcome == "inconclusive"


def test_rollout_time_does_not_count_as_observation() -> None:
    clock = FakeClock()
    backend = Backend(clock, Plan(rollout_seconds=8.0))
    assert Monitor(backend, backend, clock).run(inputs()).outcome == "pass"
    assert clock.now >= 1068


def test_poll_interval_cannot_extend_rollout_deadline() -> None:
    clock = FakeClock()
    backend = Backend(clock, Plan(rollout_seconds=2.0))
    result = Monitor(backend, backend, clock).run(inputs(rollout_timeout_seconds=1.0, poll_seconds=5.0))
    assert result.outcome == "fail"
    assert clock.now == 1001


def test_naive_no_errors_check_false_passes_missing_logs() -> None:
    clock = FakeClock()
    backend = Backend(clock, Plan(fault="missing_logs"))
    receipt = backend.deploy(DeployRequest(service="api", version="v2", request_key="naive"))
    clock.sleep(61)
    page = backend.read(LogQuery(deployment_id=receipt.deployment_id, start=1001.0, end=1061.0))
    assert not any(event.level == "error" for event in page.events)  # naive implementation says PASS
    assert page.complete_through < 1061  # independent counter-evidence
    result = Monitor(backend, backend, clock).run(inputs(request_key="correct"))
    assert result.outcome == "inconclusive"


def test_deployment_idempotency_and_conflicts(tmp_path: Path) -> None:
    store = Store(tmp_path / "deployments.sqlite")
    request = DeployRequest(service="api", version="v2", request_key="same")
    first = store.deploy(request, Plan(), 1000.0)
    assert store.deploy(request, Plan(), 2000.0) == first
    assert store.get(first.deployment_id).accepted_at == 1000
    with pytest.raises(ValueError, match="conflict"):
        store.deploy(DeployRequest(service="api", version="v3", request_key="same"), Plan(), 3000.0)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:80",
        "http://127.0.0.1:80@evil.test",
        "http://127.0.0.1:80/path",
        "http://127.0.0.1:80?x=y",
    ],
)
def test_simulation_never_targets_external_systems(url: str) -> None:
    with pytest.raises(ValueError):
        local_url(url)
