"""Local fixture lifecycle. Child processes are always reaped, including on failure."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .fixtures import Plan
from .workflow.monitoring.http import LocalHTTP

PROJECT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Endpoints:
    deploy: str
    logs: str
    database: Path


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def servers(directory: Path, plan: Plan) -> Iterator[Endpoints]:
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / "plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2))
    database = directory / "deployments.sqlite"
    urls: list[str] = []
    with ExitStack() as stack:
        for kind in ("deploy", "logs"):
            ready = directory / f"{kind}.port"
            ready.unlink(missing_ok=True)
            log = stack.enter_context((directory / f"{kind}.log").open("wb"))
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scenarios.deploy_monitor.server",
                    kind,
                    "--database",
                    str(database),
                    "--plan",
                    str(plan_path),
                    "--ready",
                    str(ready),
                ],
                cwd=PROJECT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
            stack.callback(stop, process)
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"{kind} mock failed to start; see {directory / (kind + '.log')}")
                time.sleep(0.05)
            url = "http://127.0.0.1:" + ready.read_text().strip()
            LocalHTTP(url).request("/health")
            urls.append(url)
        yield Endpoints(deploy=urls[0], logs=urls[1], database=database)
