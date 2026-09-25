"""Disposable fixture subprocesses and private evidence files; not an OS sandbox."""

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .contracts import Environment, Incident, Observation, ReplayUnavailable, Result


def workspace_file(workspace: Path, relative: str) -> Path:
    candidate = workspace / relative
    resolved = candidate.resolve()
    if Path(relative).is_absolute() or not resolved.is_relative_to(workspace.resolve()):
        raise ValueError("Fixture database must stay within the selected workspace")
    return resolved


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()


@dataclass(frozen=True)
class LocalEvidence:
    directory: Path

    def snapshot(self, incident: Incident) -> None:
        raw = incident.model_dump_json().encode()
        (self.directory / "incident.json").write_bytes(raw)
        (self.directory / "incident.sha256").write_text(hashlib.sha256(raw).hexdigest())

    def attempts(self, result: Result) -> None:
        (self.directory / "attempts.json").write_text(result.model_dump_json())


@dataclass(frozen=True)
class FixtureRunner:
    project: Path
    database: Path
    execution_id: str

    def _invoke(self, arguments: tuple[str, ...], timeout: float) -> bytes:
        try:
            process = subprocess.run(
                [
                    sys.executable,
                    "-s",
                    "-m",
                    "scenarios.task_replay.worker",
                    "--database",
                    str(self.database),
                    "--execution",
                    self.execution_id,
                    *arguments,
                ],
                cwd=self.project,
                env={"PATH": os.defpath},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReplayUnavailable("fixture_worker_unavailable") from error
        if process.returncode != 0 or len(process.stdout) > 65536:
            raise ReplayUnavailable("fixture_worker_failed")
        return process.stdout

    def describe(self, timeout: float) -> Environment:
        try:
            return Environment.model_validate_json(self._invoke(("--describe",), timeout))
        except ValueError as error:
            raise ReplayUnavailable("invalid_environment") from error

    def execute(self, incident: Incident, attempt: int, timeout: float) -> Observation:
        with tempfile.TemporaryDirectory(prefix="owlmatic-replay-") as directory:
            root = Path(directory)
            source = root / "incident.json"
            source.write_text(incident.model_dump_json())
            try:
                return Observation.model_validate_json(
                    self._invoke(
                        ("--incident", str(source), "--scratch", str(root), "--attempt", str(attempt)),
                        timeout,
                    )
                )
            except ValueError as error:
                raise ReplayUnavailable("invalid_or_missing_execution_result") from error
