"""Real-process lifecycle tests use readiness handshakes, not assumed startup timing."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from owlmatic.infrastructure.run_guard import FileRunGuard


def wait_for(path: Path, seconds: float = 8) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), f"Missing process handshake: {path.name}"


def child_script(root: Path, name: str = "child") -> str:
    return f"""import os, signal, sys, time
from pathlib import Path
root = Path({str(root)!r})
def stop(signum, frame):
    (root / {f"{name}.stopped"!r}).write_text("terminated")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(root / "guardian.pid").write_text(str(os.getppid()))
(root / {f"{name}.ready"!r}).write_text(str(os.getpid()))
while True: time.sleep(0.01)
"""


def supervisor_script(root: Path, script: str, timeout: float) -> str:
    return f"""from pathlib import Path
from owlmatic.infrastructure.process import SubprocessWorkflow
from owlmatic.infrastructure.run_guard import FileRunGuard
from owlmatic.infrastructure.system import SystemClock
from owlmatic.ports import ProcessRequest
from owlmatic.errors import OwlError
class Observer:
    def output(self, *args, **kwargs): pass
    def cancelled(self): return False
    def heartbeat(self): pass
try:
    SubprocessWorkflow(SystemClock(), FileRunGuard(Path({str(root)!r}))).execute(
        ProcessRequest(argv=({sys.executable!r}, "-c", {script!r}), directory=Path({str(root)!r}),
                       environment={{}}, timeout_seconds={timeout!r}, max_output_bytes=4096,
                       run_id="run_" + "e" * 32), Observer())
except OwlError:
    pass
"""


@pytest.mark.parametrize("interruption", [signal.SIGKILL, signal.SIGSTOP])
def test_guardian_survives_worker_death_or_suspension(tmp_path: Path, interruption: int) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", supervisor_script(tmp_path, child_script(tmp_path), 1.5)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(tmp_path / "child.ready")
        os.kill(process.pid, interruption)
        # EOF handles death; the independent deadline handles a frozen worker.
        wait_for(tmp_path / "child.stopped")
        if interruption == signal.SIGSTOP:
            os.kill(process.pid, signal.SIGCONT)
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while True:
            with FileRunGuard(tmp_path).exclusive("run_" + "e" * 32) as idle:
                if idle:
                    break
            assert time.monotonic() < deadline, "Guardian kept its activity lock after cleanup"
            time.sleep(0.01)
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
            process.kill()
            process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()


def test_normal_completion_stops_background_descendants(tmp_path: Path) -> None:
    descendant = child_script(tmp_path, "descendant")
    command = f"""import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, "-c", {descendant!r}])
ready=Path({str(tmp_path / "descendant.ready")!r})
while not ready.exists(): time.sleep(0.01)
"""
    result = subprocess.run(
        [sys.executable, "-c", supervisor_script(tmp_path, command, 5.0)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "descendant.stopped").exists()


def test_worker_stops_workflow_if_guardian_dies(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", supervisor_script(tmp_path, child_script(tmp_path), 10.0)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(tmp_path / "child.ready")
        guardian_pid = int((tmp_path / "guardian.pid").read_text())
        os.kill(guardian_pid, signal.SIGKILL)
        wait_for(tmp_path / "child.stopped")
        process.wait(timeout=5)
        with FileRunGuard(tmp_path).exclusive("run_" + "e" * 32) as idle:
            assert idle
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()


def test_unacknowledged_launch_never_executes_workflow(tmp_path: Path) -> None:
    from owlmatic.infrastructure import process_guardian

    gate_read, gate_write = os.pipe()
    os.close(gate_write)
    marker = tmp_path / "must-not-execute"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(process_guardian.__file__)),
                "_exec",
                str(gate_read),
                sys.executable,
                "-c",
                f"open({str(marker)!r}, 'w').close()",
            ],
            pass_fds=(gate_read,),
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 125
        assert not marker.exists()
    finally:
        os.close(gate_read)
