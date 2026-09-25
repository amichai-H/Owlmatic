from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from owlmatic.domain import Run, Target
from owlmatic.errors import OwlError
from owlmatic.infrastructure.run_repository import SqliteRuns
from owlmatic.infrastructure.sqlite import SqliteDatabase
from tests.fakes import FakeClock


def invocation(index: int) -> Run:
    return Run(
        run_id=f"run_{index:032x}",
        workflow_ref="fixture@digest",
        effects="unknown",
        target=Target(environment="simulation", workspace="/fixture"),
        started_at="fixed",
        evidence_ref="fixture",
    )


def test_concurrent_duplicate_requests_launch_only_once(tmp_path: Path) -> None:
    repository = SqliteRuns(SqliteDatabase(tmp_path), FakeClock())

    def submit(index: int) -> str:
        return repository.create(invocation(index), "request", "same-input", "resource").run_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))
    assert len(set(results)) == 1
    with pytest.raises(OwlError) as caught:
        repository.create(invocation(99), "request", "different-input", "resource")
    assert caught.value.code == "REQUEST_CONFLICT"


def test_worker_loss_keeps_lock_until_explicit_reconciliation(tmp_path: Path) -> None:
    clock = FakeClock()
    repository = SqliteRuns(SqliteDatabase(tmp_path), clock)
    first = repository.create(invocation(1), None, "a", "resource")
    clock.sleep(31)
    lost = repository.get(first.run_id)
    assert lost.state == "error" and lost.effects == "unknown"
    assert lost.error and lost.error.code == "WORKER_LOST"
    assert not repository.expired(clock.epoch() + 1)
    with pytest.raises(OwlError) as caught:
        repository.create(invocation(2), None, "b", "resource")
    assert caught.value.code == "BUSY"
    repository.settled(first.run_id, release_lock=False)
    repository.release_lock(first.run_id)
    assert repository.create(invocation(2), None, "b", "resource").run_id != first.run_id
