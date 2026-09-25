import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from owlmatic.errors import OwlError
from owlmatic.infrastructure.process import SubprocessWorkflow
from owlmatic.infrastructure.system import SystemClock
from owlmatic.ports import ProcessRequest
from tests.fakes import FakeClock


@dataclass
class Observer:
    output_bytes: bytearray = field(default_factory=bytearray)
    cancellation_requested: bool = False
    cancel_on_heartbeat: bool = False

    def output(self, stream: str, data: bytes, final: bool = False) -> None:
        self.output_bytes.extend(data)

    def cancelled(self) -> bool:
        return self.cancellation_requested

    def heartbeat(self) -> None:
        if self.cancel_on_heartbeat:
            self.cancellation_requested = True


def request(tmp_path: Path, script: str, *, limit: int = 4096) -> ProcessRequest:
    return ProcessRequest(
        argv=(sys.executable, "-c", script),
        directory=tmp_path,
        environment={},
        timeout_seconds=5,
        max_output_bytes=limit,
    )


def test_real_process_streams_and_returns_exit_code(tmp_path: Path) -> None:
    observer = Observer()
    code = SubprocessWorkflow(SystemClock()).execute(
        request(tmp_path, "print('fixture'); raise SystemExit(1)"), observer
    )
    assert code == 1 and observer.output_bytes == b"fixture\n"


def test_cancellation_before_launch_has_no_effects(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    with pytest.raises(OwlError) as caught:
        SubprocessWorkflow(SystemClock()).execute(
            request(tmp_path, f"open({str(marker)!r}, 'w').close()"), Observer(cancellation_requested=True)
        )
    assert caught.value.code == "CANCELLED"
    assert not marker.exists()


def test_cancellation_terminates_running_process(tmp_path: Path) -> None:
    with pytest.raises(OwlError) as caught:
        SubprocessWorkflow(SystemClock()).execute(
            request(tmp_path, "import time; time.sleep(60)"), Observer(cancel_on_heartbeat=True)
        )
    assert caught.value.code == "CANCELLED"


def test_output_limit_stops_chatty_process(tmp_path: Path) -> None:
    with pytest.raises(OwlError) as caught:
        SubprocessWorkflow(SystemClock()).execute(
            request(tmp_path, "print('x' * 8192)", limit=1024), Observer()
        )
    assert caught.value.code == "OUTPUT_LIMIT"


class DeadlineClock(FakeClock):
    def monotonic(self) -> float:
        self.now += 10
        return self.now


def test_deadline_uses_injected_clock(tmp_path: Path) -> None:
    with pytest.raises(OwlError) as caught:
        SubprocessWorkflow(DeadlineClock()).execute(
            request(tmp_path, "import time; time.sleep(60)"), Observer()
        )
    assert caught.value.code == "TIMEOUT"
