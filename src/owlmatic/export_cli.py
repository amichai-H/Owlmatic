"""Export argument mapping only. Delivery and consent decisions live in services."""

import argparse
from pathlib import Path

from pydantic import BaseModel

from .export_service import ExportService


def arguments(parser: argparse.ArgumentParser) -> None:
    children = parser.add_subparsers(dest="action", required=True)
    for name in ("configure", "status", "preview", "push", "retry", "disable"):
        child = children.add_parser(name)
        child.add_argument("--json", action="store_true")
        if name == "configure":
            child.add_argument("--file", required=True, type=Path)


def dispatch(service: ExportService, args: argparse.Namespace) -> BaseModel:
    match args.action:
        case "configure":
            return service.configure(args.file.expanduser().absolute())
        case "status":
            return service.status()
        case "preview":
            return service.preview()
        case "push":
            return service.push()
        case "retry":
            return service.push(retry=True)
        case "disable":
            return service.disable()
        case _:
            raise ValueError("Unknown export action")
