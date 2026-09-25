"""Fast real-HTTP integration matrix; the one-minute demo remains a separate test."""

import tempfile
import time
import uuid
from pathlib import Path

from .fixtures import Fault, Plan
from .runtime import servers
from .workflow.monitoring.contracts import Inputs
from .workflow.monitoring.entrypoint import execute

CASES: tuple[tuple[Fault, str], ...] = (
    ("healthy", "pass"),
    ("boundary_error", "fail"),
    ("wrong_version", "fail"),
    ("missing_replica", "fail"),
    ("missing_logs", "inconclusive"),
    ("gap", "inconclusive"),
    ("cursor_loop", "inconclusive"),
    ("malformed", "inconclusive"),
    ("unavailable", "inconclusive"),
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="owlmatic-live-matrix-") as temporary:
        for fault, expected in CASES:
            with servers(
                Path(temporary) / fault,
                Plan(
                    fault=fault,
                    rollout_seconds=0.1,
                    error_after_seconds=1.2,
                    ingestion_delay_seconds=0.2 if fault == "boundary_error" else 0.0,
                ),
            ) as endpoints:
                inputs = Inputs(
                    deploy_url=endpoints.deploy,
                    logs_url=endpoints.logs,
                    service="checkout",
                    version="v2.4.0",
                    request_key=uuid.uuid4().hex,
                    monitor_minutes=0.02,
                    poll_seconds=0.1,
                    rollout_timeout_seconds=2.0,
                    ingestion_grace_seconds=0.5,
                )
                started = time.monotonic()
                result = execute(inputs)
                if result.outcome != expected:
                    raise RuntimeError(f"{fault}: expected {expected}, got {result.model_dump_json()}")
                if expected == "pass" and time.monotonic() - started < 1.2:
                    raise RuntimeError("Passed before the real observation window elapsed")
                print(f"{fault}: {result.outcome} ({result.reason})", flush=True)


if __name__ == "__main__":
    main()
