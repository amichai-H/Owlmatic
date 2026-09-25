"""CLI transport: parse, invoke application services, render, select exit status."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import BaseModel

from .bootstrap import Application, create_application
from .cli_parser import parser
from .domain import Catalog, InspectRequest, RunRequest, RunSummary, SearchRequest
from .errors import OwlError
from .export_cli import dispatch as export_dispatch
from .export_state import DeliveryReport
from .failures import public_failure
from .measurement_cli import dispatch as measurement_dispatch
from .messages import ValidationReport
from .serialization import encode, json_object
from .statistics import SavingsBaseline, StatisticsRequest


def dispatch(app: Application, args: argparse.Namespace) -> BaseModel:
    match args.command:
        case "measure":
            return measurement_dispatch(app.measurements, args)
        case "export":
            return export_dispatch(app.exports, args)
        case "find":
            return app.catalog.find(
                SearchRequest(
                    query=args.query,
                    environment=args.environment,
                    repository=args.repository,
                    profile=args.profile,
                    limit=args.limit,
                )
            )
        case "describe":
            return app.catalog.describe(args.ref)
        case "run":
            raw = args.input_file.read(16385) if args.input_file else args.input or "{}"
            if args.input_file:
                args.input_file.close()
            return app.execution.run(
                RunRequest(
                    ref=args.ref,
                    inputs=json_object(raw),
                    profile=args.profile,
                    environment=args.environment,
                    workspace=Path(args.workspace).expanduser().resolve(),
                    request_id=args.request_id,
                    wait_seconds=args.wait,
                )
            )
        case "inspect":
            return app.execution.inspect(
                InspectRequest(
                    run_id=args.run_id,
                    view=args.view,
                    cursor=args.cursor,
                    max_bytes=args.max_bytes,
                    wait_seconds=args.wait,
                )
            )
        case "cancel":
            return app.execution.cancel(args.run_id)
        case "reconcile":
            return app.execution.reconcile(args.run_id)
        case "trust" | "revoke":
            return app.catalog.trust(args.ref, args.profile, revoke=args.command == "revoke")
        case "catalog":
            if args.action == "add":
                source = (
                    str(Path(args.source).expanduser().absolute()) if args.kind == "local" else args.source
                )
                return app.catalog.add(
                    Catalog(name=args.name, source=source, kind=args.kind, revision=args.revision)
                )
            if args.action == "sync":
                return app.catalog.sync(args.name)
            return app.catalog.list()
        case "capture":
            return app.authoring.capture(
                args.name,
                Path(args.output).expanduser().absolute() if args.output else None,
                args.source_task,
            )
        case "validate":
            return app.authoring.validate(
                Path(args.directory).absolute(), args.profile, args.environment, args.schema_only
            )
        case "profile":
            return app.administration.create_profile(
                args.name, tuple(args.environment), tuple(args.env), tuple(args.credential)
            )
        case "setup":
            return app.administration.setup(args.host, Path(args.project).expanduser().resolve(), args.apply)
        case "examples":
            return app.examples()
        case "recover":
            return app.recovery.recover()
        case "cleanup":
            return app.execution.cleanup(args.days)
        case "doctor":
            return app.diagnostics()
        case "stats":
            if args.action == "baseline":
                return app.statistics.baseline(
                    SavingsBaseline(
                        ref=args.ref,
                        manual_tokens=args.manual_tokens,
                        owlmatic_tokens=args.owlmatic_tokens,
                        setup_tokens=args.setup_tokens,
                        basis=args.basis,
                        source=args.source,
                        sample_size=args.sample_size,
                    )
                )
            return app.statistics.report(StatisticsRequest(days=args.days))
        case "dashboard":
            return app.dashboard.generate(
                StatisticsRequest(days=args.days), Path(args.output) if args.output else None
            )
        case _:
            raise OwlError("UNKNOWN_COMMAND", "Unsupported command")


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "mcp":
            from .mcp_server import create_server

            create_server().run(transport="stdio")
            return
        app = create_application()
        if args.command == "measure" and args.action == "watch":
            for report in app.measurements.watch(args.once):
                print(encode(report), flush=True)
            return
        if args.command == "measure" and args.action == "prepare-tokenizer":
            print(encode(app.prepare_tokenizer()))
            return
        result = dispatch(app, args)
        rendered = encode(result) if args.json else result.model_dump_json(indent=2, exclude_none=True)
        print(rendered)
        app.record_response(
            args.command, rendered, result.run.run_id if isinstance(result, RunSummary) else None
        )
        if isinstance(result, ValidationReport) and not result.valid:
            raise SystemExit(1)
        if isinstance(result, DeliveryReport) and result.status in {"failed", "deferred"}:
            raise SystemExit(1)
        if isinstance(result, RunSummary):
            if result.run.outcome == "fail":
                raise SystemExit(1)
            if result.run.state in {"error", "cancelled", "blocked"}:
                raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(encode(public_failure(error)))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
