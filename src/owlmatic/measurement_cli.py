"""Argument validation and service delegation; accounting stays in application services."""

import argparse
from pathlib import Path

from pydantic import BaseModel

from .measurement.contracts import BaselineLink, CostCoverage, ImportRequest
from .measurement.service import MeasurementService


def arguments(parser: argparse.ArgumentParser) -> None:
    children = parser.add_subparsers(dest="action", required=True)
    for name in ("import", "watch", "baseline", "report", "coverage", "prepare-tokenizer"):
        child = children.add_parser(name)
        child.add_argument("--json", action="store_true")
        if name == "import":
            child.add_argument("--host", choices=("codex", "claude"), required=True)
            child.add_argument("--session", type=Path, required=True)
            child.add_argument("--task-id", required=True)
            child.add_argument(
                "--purpose", choices=("manual", "reuse", "creation", "maintenance"), default="reuse"
            )
            child.add_argument("--workflow-ref")
            child.add_argument("--run-id", action="append", default=[])
            child.add_argument("--model")
            child.add_argument("--workload", default="default")
            child.add_argument("--verified", action="store_true")
            child.add_argument("--first-line", type=int, default=1)
            child.add_argument("--last-line", type=int)
        elif name in {"baseline", "coverage"}:
            child.add_argument("ref")
            if name == "baseline":
                child.add_argument("--task", required=True)
            else:
                child.add_argument("--setup-complete", action="store_true")
                child.add_argument("--maintenance-complete", action="store_true")
        elif name == "report":
            child.add_argument("ref", nargs="?")
        elif name == "watch":
            child.add_argument("--once", action="store_true")


def dispatch(service: MeasurementService, args: argparse.Namespace) -> BaseModel:
    if args.action == "import":
        return service.ingest(
            ImportRequest(
                host=args.host,
                session=args.session,
                task_id=args.task_id,
                purpose=args.purpose,
                workflow_ref=args.workflow_ref,
                run_ids=tuple(args.run_id),
                model=args.model,
                workload=args.workload,
                verified=args.verified,
                first_line=args.first_line,
                last_line=args.last_line,
            )
        )
    if args.action == "baseline":
        return service.baseline(BaselineLink(workflow_ref=args.ref, task_id=args.task))
    if args.action == "coverage":
        return service.declare(
            CostCoverage(
                workflow_ref=args.ref,
                setup_complete=args.setup_complete,
                maintenance_complete=args.maintenance_complete,
            )
        )
    return service.assessment(args.ref) if args.ref else service.report()
