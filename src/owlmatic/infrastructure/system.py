from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..domain import Target
from ..errors import OwlError
from ..ports import CommandExecutor, ProcessRequest
from .files import safe_path
from .process import SubprocessWorkflow


class SystemClock:
    def epoch(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class CommandCapture:
    def __init__(self) -> None:
        self.stdout = bytearray()

    def output(self, stream: str, data: bytes, final: bool = False) -> None:
        if stream == "stdout":
            self.stdout.extend(data)

    def cancelled(self) -> bool:
        return False

    def heartbeat(self) -> None:
        pass


class SystemCommands:
    def execute(self, args: Sequence[str], timeout: float = 120, max_bytes: int = 67108864) -> bytes:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file:https:ssh"}
        capture = CommandCapture()
        try:
            code = SubprocessWorkflow(SystemClock()).execute(
                ProcessRequest(
                    argv=args,
                    directory=Path.cwd(),
                    environment=env,
                    timeout_seconds=timeout,
                    max_output_bytes=max_bytes,
                ),
                capture,
            )
        except OwlError as error:
            if error.code in {"TIMEOUT", "OUTPUT_LIMIT"}:
                raise OwlError("COMMAND_LIMIT", "External command exceeded time or output limits") from error
            raise OwlError("COMMAND_FAILED", "External command could not complete") from error
        if code != 0:
            raise OwlError(
                "COMMAND_FAILED", "External command failed; check prerequisites and authentication"
            )
        return bytes(capture.stdout)


class SystemHost:
    def __init__(self, commands: CommandExecutor) -> None:
        self.commands = commands

    @property
    def platform(self) -> str:
        return sys.platform

    @property
    def environment(self) -> Mapping[str, str]:
        return os.environ

    def command_exists(self, command: str, directory: Path, path: str) -> bool:
        if "/" in command:
            file = safe_path(directory, command)
            return file.is_file() and os.access(file, os.X_OK)
        return shutil.which(command, path=path) is not None

    def workspace_exists(self, workspace: Path) -> bool:
        return workspace.is_dir()

    def required_path_exists(self, workspace: Path, path: str) -> bool:
        return safe_path(workspace, path).exists()

    def target(self, workspace: Path, environment: str) -> Target:
        try:
            args = ["git", "-C", str(workspace)]
            revision = self.commands.execute([*args, "rev-parse", "HEAD"], timeout=2).decode().strip()
            changed = self.commands.execute(
                [*args, "status", "--porcelain", "--untracked-files=normal"], timeout=2
            )
            return Target(
                environment=environment,
                workspace=str(workspace),
                source_revision=revision,
                dirty=bool(changed),
            )
        except OwlError:
            return Target(environment=environment, workspace=str(workspace))


class SubprocessLauncher:
    def __init__(self, root: Path) -> None:
        self.root = root

    def start(self, run_id: str, environment: Mapping[str, str]) -> int:
        env = {
            **environment,
            "OWLMATIC_HOME": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "owlmatic.worker", run_id],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=self.root / "runs" / run_id,
            start_new_session=True,
        )
        return process.pid
