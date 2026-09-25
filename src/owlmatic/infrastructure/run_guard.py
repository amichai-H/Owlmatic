"""Cross-process activity locks. Never unlink lock files while participants may exist."""

import fcntl
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..errors import OwlError
from .files import private_directory


class FileRunGuard:
    def __init__(self, root: Path) -> None:
        self.root = private_directory(root / "guards")

    def _open(self, run_id: str) -> int:
        if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise OwlError("INVALID_RUN", "Invalid run ID")
        return os.open(self.root / run_id, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)

    @contextmanager
    def shared_descriptor(self, run_id: str) -> Iterator[int]:
        fd = self._open(run_id)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            yield fd
        finally:
            # Close only: LOCK_UN would release the guardian's inherited lock too.
            os.close(fd)

    @contextmanager
    def hold(self, run_id: str) -> Iterator[None]:
        with self.shared_descriptor(run_id):
            yield

    @contextmanager
    def exclusive(self, run_id: str) -> Iterator[bool]:
        fd = self._open(run_id)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                yield True
        finally:
            os.close(fd)
