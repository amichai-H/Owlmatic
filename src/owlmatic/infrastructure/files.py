from __future__ import annotations

import fcntl
import re
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from ..domain import Evidence, Failure, InspectRequest, Job, Page, Run, Settings
from ..errors import OwlError
from ..serialization import atomic_model, atomic_text, payload_json, read_model
from .logs import RedactedLog


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def safe_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise OwlError("PATH_ESCAPE", "Paths must remain inside their declared root")
    cursor = root
    for part in path.parts:
        cursor /= part
        if cursor.is_symlink():
            raise OwlError("SYMLINK", "Symlinks are not permitted in workflow paths")
    return cursor


class FileSettings:
    def __init__(self, root: Path) -> None:
        self.root = private_directory(root)
        self.path = root / "config.json"
        self.update(lambda settings: settings)

    def load(self) -> Settings:
        return read_model(self.path, Settings)

    def save(self, settings: Settings) -> None:
        self.update(lambda _: settings)

    def update(self, change: Callable[[Settings], Settings]) -> Settings:
        lock_path = self.root / "config.lock"
        with lock_path.open("a") as lock:
            lock_path.chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = read_model(self.path, Settings) if self.path.exists() else Settings()
            updated = change(current)
            atomic_model(self.path, updated)
            return updated


class FileArtifacts:
    def __init__(self, root: Path) -> None:
        self.root = private_directory(root / "runs")

    def directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise OwlError("INVALID_RUN", "Invalid run ID")
        return self.root / run_id

    def prepare(self, job: Job) -> Path:
        path = private_directory(self.directory(job.run.run_id))
        private_directory(path / "artifacts")
        atomic_model(path / "job.json", job)
        atomic_text(path / "inputs.json", payload_json(job.inputs))
        atomic_model(path / "result.json", job.run)
        return path

    def job(self, run_id: str) -> Job:
        return read_model(self.directory(run_id) / "job.json", Job)

    def save_result(self, run: Run) -> None:
        atomic_model(private_directory(self.directory(run.run_id)) / "result.json", run)

    def evidence(self, run_id: str) -> Evidence | None:
        path = self.directory(run_id) / "evidence.json"
        return read_model(path, Evidence) if path.exists() else None

    def save_evidence(self, evidence: Evidence) -> None:
        atomic_model(self.directory(evidence.run_id) / "evidence.json", evidence)

    def raw_evidence(self, run_id: str) -> Evidence:
        path = self.directory(run_id) / "raw-result.json"
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
            raise OwlError("MISSING_EVIDENCE", "Expected a regular evidence file of at most 64 KiB")
        return read_model(path, Evidence)

    def log(self, run_id: str, secrets: Sequence[str], max_bytes: int) -> RedactedLog:
        return RedactedLog(self.directory(run_id) / "events.jsonl", secrets, max_bytes)

    def request_cancel(self, run_id: str) -> None:
        (self.directory(run_id) / "cancel").touch(mode=0o600)

    def cancelled(self, run_id: str) -> bool:
        return (self.directory(run_id) / "cancel").exists()

    def remove(self, run_id: str) -> None:
        shutil.rmtree(self.directory(run_id), ignore_errors=True)

    def page(self, request: InspectRequest) -> Page:
        names = {
            "logs": "events.jsonl",
            "evidence": "evidence.json",
            "result": "result.json",
            "diagnostic": "diagnostic.json",
        }
        if request.view not in names:
            raise OwlError("INVALID_VIEW", "This view does not expose a paged artifact")
        path = self.directory(request.run_id) / names[request.view]
        if not path.exists():
            return Page(run_id=request.run_id, view=request.view, text="", next_cursor=None)
        if path.is_symlink():
            raise OwlError("SYMLINK", "Artifact links cannot be retrieved")
        with path.open("rb") as stream:
            stream.seek(request.cursor)
            data = stream.read(max(1, (request.max_bytes - 160) // 6))
        next_cursor = request.cursor + len(data)
        return Page(
            run_id=request.run_id,
            view=request.view,
            text=data.decode("utf-8", errors="replace"),
            next_cursor=next_cursor if next_cursor < path.stat().st_size else None,
        )

    def diagnostic(self, run_id: str, failure: Failure) -> None:
        # Application failures contain safe codes/messages, never exception reprs.
        atomic_model(self.directory(run_id) / "diagnostic.json", failure)

    def scrub_inputs(self, run_id: str) -> None:
        path = self.directory(run_id)
        failures: list[OSError] = []
        for filename in ("raw-result.json", "inputs.json", "job.json"):
            try:
                (path / filename).unlink(missing_ok=True)
            except OSError as error:
                failures.append(error)
        if failures:
            raise OwlError("CLEANUP_PENDING", "Private input removal was incomplete") from failures[0]
