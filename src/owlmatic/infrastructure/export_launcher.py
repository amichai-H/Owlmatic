"""Start a separate metrics observer with only its own credential binding."""

import os
import subprocess
import sys
from pathlib import Path


class SubprocessExportLauncher:
    def __init__(self, root: Path) -> None:
        self.root = root

    def start(self, run_id: str, timeout: int, token_env: str | None) -> None:
        environment = {
            "OWLMATIC_HOME": str(self.root),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if token_env and token_env in os.environ:
            environment[token_env] = os.environ[token_env]
        subprocess.Popen(
            [sys.executable, "-m", "owlmatic.export_worker", run_id, str(timeout)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
