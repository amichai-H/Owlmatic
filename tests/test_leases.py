from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from owlmatic.domain import CheckCounts, Failure
from owlmatic.errors import OwlError
from owlmatic.infrastructure.run_repository import SqliteRuns
from owlmatic.infrastructure.sqlite import SqliteDatabase
from tests.fakes import FakeClock
from tests.test_storage import invocation


def test_stale_worker_cannot_change_expired_result(tmp_path: Path) -> None:
    clock = FakeClock()
    runs = SqliteRuns(SqliteDatabase(tmp_path), clock)
    original = runs.create(invocation(1), None, "fingerprint", "resource")
    runs.claim(original.run_id, "old-worker")
    clock.sleep(31)
    lost = runs.get(original.run_id)
    success = original.finish(
        state="completed",
        outcome="pass",
        effects="completed",
        finished_at=clock.iso(),
        checks=CheckCounts(required=1, passed=1, failed=0, skipped=0),
    )
    with pytest.raises(OwlError, match="stale worker"):
        runs.finish(success, "old-worker")
    with pytest.raises(OwlError) as caught:
        runs.heartbeat(original.run_id, "old-worker")
    assert caught.value.code == "LEASE_LOST"
    assert runs.get(original.run_id) == lost


def test_duplicate_workers_cannot_claim_same_run(tmp_path: Path) -> None:
    runs = SqliteRuns(SqliteDatabase(tmp_path), FakeClock())
    run = runs.create(invocation(1), None, "fingerprint", None)
    barrier = Barrier(8)

    def claim(index: int) -> bool:
        barrier.wait(timeout=5)
        try:
            runs.claim(run.run_id, str(index))
            return True
        except OwlError as error:
            assert error.code == "LEASE_LOST"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(claim, range(8))) == 1


def test_concurrent_completion_has_one_authoritative_result(tmp_path: Path) -> None:
    runs = SqliteRuns(SqliteDatabase(tmp_path), FakeClock())
    run = runs.create(invocation(1), None, "fingerprint", None)
    runs.claim(run.run_id, "owner")
    candidates = (
        run.finish(
            state="completed",
            outcome="pass",
            effects="completed",
            finished_at="fixed",
            checks=CheckCounts(required=1, passed=1, failed=0, skipped=0),
        ),
        run.finish(
            state="cancelled",
            outcome="inconclusive",
            effects="unknown",
            finished_at="fixed",
            error=Failure(code="CANCELLED", message="cancelled"),
        ),
    )
    barrier = Barrier(2)

    def finish(index: int) -> bool:
        barrier.wait(timeout=5)
        try:
            runs.finish(candidates[index], "owner")
            return True
        except OwlError as error:
            assert error.code == "LEASE_LOST"
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(finish, range(2))) == 1
    terminal = runs.get(run.run_id)
    assert terminal in candidates
    with pytest.raises(OwlError):
        runs.finish(candidates[0], "owner")
    assert runs.get(run.run_id) == terminal


def test_expired_lease_cannot_be_renewed_without_inspection(tmp_path: Path) -> None:
    clock = FakeClock()
    runs = SqliteRuns(SqliteDatabase(tmp_path), clock)
    run = runs.create(invocation(1), None, "fingerprint", None)
    runs.claim(run.run_id, "owner")
    clock.sleep(31)
    with pytest.raises(OwlError) as caught:
        runs.heartbeat(run.run_id, "owner")
    assert caught.value.code == "LEASE_LOST"
    assert runs.get(run.run_id).state == "error"
