"""Private stdlib-only watchdog, launched with Python isolated mode.

A pipe ties its lifetime to the supervising worker. It owns the workflow process
group and an independent deadline, so worker SIGKILL cannot disable cleanup.
The inherited activity-lock descriptor stays open until process cleanup finishes.
"""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import subprocess
import sys
import time
from types import FrameType

STOP = False


def request_stop(signum: int, frame: FrameType | None) -> None:
    global STOP
    STOP = True


def terminate(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 0.5
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    # The group leader can have exited while descendants still need their grace
    # period. Waiting only for the leader would immediately SIGKILL those children.
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(control_fd: int, status_fd: int, activity_fd: int, timeout: float, argv: list[str]) -> None:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    status = "GUARD_FAILED"
    process: subprocess.Popen[bytes] | None = None
    gate_write: int | None = None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(control_fd, selectors.EVENT_READ)
            # If the worker died before launch, no workflow should be started.
            if selector.select(timeout=0) and not os.read(control_fd, 1):
                status = "OWNER_LOST"
                return
            gate_read, gate_write = os.pipe()
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", __file__, "_exec", str(gate_read), *argv],
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    pass_fds=(gate_read, activity_fd) if activity_fd >= 0 else (gate_read,),
                )
            finally:
                os.close(gate_read)
            os.write(status_fd, f"pid:{process.pid}\n".encode("ascii"))
            deadline = time.monotonic() + timeout
            while True:
                if STOP:
                    status = "CANCELLED"
                    break
                code = process.poll()
                if code is not None:
                    status = f"exit:{code}"
                    break
                if time.monotonic() >= deadline:
                    status = "TIMEOUT"
                    break
                if selector.select(timeout=min(0.05, max(0.0, deadline - time.monotonic()))):
                    command = os.read(control_fd, 1)
                    if not command:
                        status = "OWNER_LOST"
                        break
                    if command == b"S" and gate_write is not None:
                        os.write(gate_write, b"S")
                        os.close(gate_write)
                        gate_write = None
    except OSError:
        status = "PROCESS_START_FAILED"
    finally:
        if gate_write is not None:
            os.close(gate_write)
        if process is not None:
            # Also stop background descendants left after a successful main process.
            terminate(process)
        try:
            os.write(status_fd, (status + "\n").encode("ascii"))
        except OSError:
            pass


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "_exec":
        gate = int(sys.argv[2])
        try:
            authorized = os.read(gate, 1) == b"S"
        finally:
            os.close(gate)
        if not authorized:
            raise SystemExit(125)
        # Preserve PID/group identity through exec. No workflow code executes until
        # the worker has received that identity and acknowledged responsibility.
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    parser = argparse.ArgumentParser()
    parser.add_argument("control_fd", type=int)
    parser.add_argument("status_fd", type=int)
    parser.add_argument("activity_fd", type=int)
    parser.add_argument("timeout", type=float)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    supervise(args.control_fd, args.status_fd, args.activity_fd, args.timeout, args.argv)


if __name__ == "__main__":
    main()
