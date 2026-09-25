"""Bounded event persistence with streaming secret redaction across chunk boundaries."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from ..errors import OwlError


class RedactedLog:
    def __init__(self, path: Path, secrets: Sequence[str], max_bytes: int) -> None:
        self.stream = path.open("w")
        path.chmod(0o600)
        self.secrets = tuple(s.encode() for s in sorted(set(secrets), key=len, reverse=True) if s)
        self.keep = max((len(s) for s in self.secrets), default=1) - 1
        self.pending: dict[str, bytes] = {"stdout": b"", "stderr": b""}
        self.bytes_written = 0
        self.limit = max_bytes

    def write(self, stream: str, data: bytes, final: bool = False) -> None:
        buffer = self.pending[stream] + data
        cut = len(buffer) if final else max(0, len(buffer) - self.keep)
        for secret in self.secrets:
            offset = buffer.find(secret)
            while offset >= 0:
                if offset < cut < offset + len(secret):
                    cut = offset
                offset = buffer.find(secret, offset + 1)
        ready, self.pending[stream] = buffer[:cut], buffer[cut:]
        for secret in self.secrets:
            ready = ready.replace(secret, b"[REDACTED]")
        if ready:
            line = json.dumps({"stream": stream, "text": ready.decode("utf-8", errors="replace")}) + "\n"
            self.bytes_written += len(line.encode())
            if self.bytes_written > self.limit:
                raise OwlError("OUTPUT_LIMIT", "Captured output exceeded the configured limit")
            self.stream.write(line)
            self.stream.flush()

    def close(self) -> None:
        # Incomplete tails are intentionally discarded on interruption: they might
        # be partial secret prefixes and do not establish a complete log event.
        self.stream.close()
