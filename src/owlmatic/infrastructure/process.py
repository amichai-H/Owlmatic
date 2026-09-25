"""Capture workflow streams while an independent guardian owns process cleanup."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from ..errors import OwlError
from ..ports import Clock, ProcessObserver, ProcessRequest
from .run_guard import FileRunGuard


@dataclass
class GuardStatus:
    child_pid: int | None = None
    result: str = "GUARD_FAILED"
    pending: bytes = b""
    received: int = 0

    def feed(self, data: bytes) -> None:
        self.received += len(data)
        if self.received > 256:
            raise OwlError("GUARD_FAILED", "Invalid guardian status")
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            text = line.decode("ascii")
            if text.startswith("pid:"):
                pid = int(text[4:])
                if pid <= 1 or self.child_pid is not None:
                    raise OwlError("GUARD_FAILED", "Invalid guardian process identity")
                self.child_pid = pid
            else:
                self.result = text


def stop_orphan(group: int) -> None:
    # Called only with the process-group identity reported by our own guardian.
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


class SubprocessWorkflow:
    def __init__(self, clock: Clock, guard: FileRunGuard | None = None) -> None:
        self.clock = clock
        self.guard = guard

    def execute(self, request: ProcessRequest, observer: ProcessObserver) -> int:
        if observer.cancelled():
            raise OwlError("CANCELLED", "Cancellation requested before process launch")
        with ExitStack() as stack:
            control_read, control_write = os.pipe()
            status_read, status_write = os.pipe()
            for fd in (control_read, control_write, status_read, status_write):
                stack.callback(os.close, fd)
            inherited = [control_read, status_write]
            activity_fd = -1
            if self.guard and request.run_id:
                activity_fd = stack.enter_context(self.guard.shared_descriptor(request.run_id))
                inherited.append(activity_fd)
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        str(Path(__file__).with_name("process_guardian.py")),
                        str(control_read),
                        str(status_write),
                        str(activity_fd),
                        str(request.timeout_seconds),
                        *request.argv,
                    ],
                    cwd=request.directory,
                    env=request.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    pass_fds=inherited,
                    start_new_session=True,
                )
            except OSError as error:
                raise OwlError("PROCESS_START_FAILED", "Could not start the execution guardian") from error
            state = GuardStatus()
            try:
                self._capture(process, request, observer, status_read, control_write, state)
                status = state.result
                if status.startswith("exit:") and process.returncode == 0:
                    return int(status[5:])
                allowed = {"TIMEOUT", "OWNER_LOST", "CANCELLED", "PROCESS_START_FAILED"}
                code = status if status in allowed else "GUARD_FAILED"
                raise OwlError(code, "Workflow supervision ended; external effects may need reconciliation")
            finally:
                if process.poll() not in {None, 0} and state.child_pid is not None:
                    stop_orphan(state.child_pid)
                # SIGTERM asks the guardian to clean up; never kill it merely to
                # make the caller return sooner. Its activity lock prevents retry
                # or cleanup if OS-level termination cannot complete promptly.
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                for stream in (process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()

    def _capture(
        self,
        process: subprocess.Popen[bytes],
        request: ProcessRequest,
        observer: ProcessObserver,
        status_fd: int,
        control_fd: int,
        state: GuardStatus,
    ) -> None:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(status_fd, False)
            selector.register(status_fd, selectors.EVENT_READ, "status")
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                assert stream is not None
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            deadline = self.clock.monotonic() + request.timeout_seconds
            last_heartbeat = self.clock.monotonic() - 1
            received = 0
            while selector.get_map() or process.poll() is None:
                if observer.cancelled():
                    raise OwlError(
                        "CANCELLED", "Cancellation requested; external effects may need reconciliation"
                    )
                if self.clock.monotonic() >= deadline:
                    raise OwlError(
                        "TIMEOUT", "Execution deadline exceeded; external effects may need reconciliation"
                    )
                if self.clock.monotonic() - last_heartbeat >= 1:
                    observer.heartbeat()
                    last_heartbeat = self.clock.monotonic()
                for event, _ in selector.select(timeout=0.1):
                    data = os.read(event.fd, 4096)
                    label = cast(str, event.data)
                    if label == "status":
                        if data:
                            previous_pid = state.child_pid
                            state.feed(data)
                            if previous_pid is None and state.child_pid is not None:
                                os.write(control_fd, b"S")
                            # The protocol ends with a final line, not EOF: the
                            # worker still has a copy of the pipe's write end.
                            if not state.result.startswith("GUARD_FAILED"):
                                selector.unregister(status_fd)
                        elif event.fd in selector.get_map():
                            selector.unregister(status_fd)
                        continue
                    if not data:
                        selector.unregister(event.fileobj)
                        observer.output(label, b"", final=True)
                        cast(BinaryIO, event.fileobj).close()
                    else:
                        received += len(data)
                        if received > request.max_output_bytes:
                            raise OwlError("OUTPUT_LIMIT", "Workflow output exceeded the configured limit")
                        observer.output(label, data)
                if process.poll() is not None:
                    if process.returncode != 0:
                        raise OwlError(
                            "GUARD_FAILED", "Guardian stopped unexpectedly; stopping the workflow group"
                        )
                    if status_fd in selector.get_map():
                        # Drain the bounded final reply even when exit wins the
                        # readiness race, then stop watching the worker's own fd.
                        try:
                            state.feed(os.read(status_fd, 256))
                        except BlockingIOError:
                            pass
                        selector.unregister(status_fd)
            process.wait()
