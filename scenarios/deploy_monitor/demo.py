"""Run a real-time local deployment scenario through Owlmatic and retain evidence."""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

from owlmatic.bootstrap import create_application
from owlmatic.domain import Catalog, RunRequest, RunSummary, SearchRequest

from .fixtures import Plan
from .runtime import PROJECT, servers
from .workflow.monitoring.contracts import Inputs


def run_workflow(home: Path, inputs: Inputs) -> RunSummary:
    app = create_application(home)
    if any(catalog.name == "deployment-lab" for catalog in app.catalog.list().catalogs):
        app.catalog.sync("deployment-lab")
    else:
        app.catalog.add(Catalog(name="deployment-lab", source=str(PROJECT / "scenarios/deploy_monitor")))
    candidate = app.catalog.find(SearchRequest(query="deploy monitor")).results[0]
    app.catalog.trust(candidate.ref)
    previous_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + previous_path
    try:
        result = app.execution.run(
            RunRequest(
                ref=candidate.ref,
                workspace=PROJECT,
                inputs=inputs.model_dump(mode="json"),
                environment="simulation",
                request_id=inputs.request_key,
            )
        )
        while result.run.state == "running":
            print(
                f"Monitoring {result.run.run_id}; full window has not completed.", file=sys.stderr, flush=True
            )
            result = RunSummary(run=app.execution.wait(result.run.run_id, 20))
        return result
    finally:
        os.environ["PATH"] = previous_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=1.0)
    parser.add_argument("--fault", default="healthy")
    parser.add_argument("--ingestion-delay", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=PROJECT / ".owlmatic/deploy-monitor")
    args = parser.parse_args()
    plan = Plan.model_validate(
        {
            "fault": args.fault,
            "ingestion_delay_seconds": args.ingestion_delay,
            "error_after_seconds": args.minutes * 60 if args.fault == "boundary_error" else args.minutes * 30,
        }
    )
    directory = args.output.resolve() / (args.fault + "-" + uuid.uuid4().hex[:8])
    with servers(directory, plan) as endpoints:
        inputs = Inputs(
            deploy_url=endpoints.deploy,
            logs_url=endpoints.logs,
            service="checkout",
            version="v2.4.0",
            request_key=uuid.uuid4().hex,
            monitor_minutes=args.minutes,
        )
        (directory / "inputs.json").write_text(inputs.model_dump_json(indent=2))
        print(
            f"Deploy: {endpoints.deploy}; logs: {endpoints.logs}; window: {args.minutes} minutes", flush=True
        )
        started = time.monotonic()
        result = run_workflow(args.output.resolve() / "home", inputs)
        (directory / "result.json").write_text(result.model_dump_json(indent=2))
        print(result.model_dump_json())
        print(f"Real elapsed seconds: {time.monotonic() - started:.2f}; evidence: {directory}")


if __name__ == "__main__":
    main()
