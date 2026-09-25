"""Argument declarations only. No workflow behavior."""

import argparse

from . import __version__
from .export_cli import arguments as export_arguments


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="owlmatic", description="Executable memory for coding agents")
    root.add_argument("--version", action="version", version=f"owlmatic {__version__}")
    sub = root.add_subparsers(dest="command", required=True)
    # argparse's parser factory is the dynamic CLI boundary; each command is mapped
    # to a validated domain request in the transport adapter.
    for name in (
        "find",
        "describe",
        "run",
        "inspect",
        "cancel",
        "reconcile",
        "recover",
        "trust",
        "revoke",
        "catalog",
        "capture",
        "validate",
        "profile",
        "setup",
        "examples",
        "cleanup",
        "doctor",
        "stats",
        "dashboard",
        "export",
        "mcp",
    ):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
        if name == "export":
            export_arguments(p)
        elif name == "find":
            p.add_argument("query")
            p.add_argument("--environment")
            p.add_argument("--repository")
            p.add_argument("--profile", default="default")
            p.add_argument("--limit", type=int, default=3)
        elif name in {"describe", "trust", "revoke", "run"}:
            p.add_argument("ref")
            if name != "describe":
                p.add_argument("--profile", default="default")
            if name == "run":
                inputs = p.add_mutually_exclusive_group()
                inputs.add_argument("--input")
                inputs.add_argument("--input-file", type=argparse.FileType("r"))
                p.add_argument("--environment")
                p.add_argument("--workspace", default=".")
                p.add_argument("--request-id")
                p.add_argument("--wait", type=float, default=20.0)
        elif name in {"inspect", "cancel", "reconcile"}:
            p.add_argument("run_id")
            if name == "inspect":
                p.add_argument(
                    "--view",
                    choices=["status", "failures", "logs", "evidence", "result", "diagnostic"],
                    default="status",
                )
                p.add_argument("--cursor", type=int, default=0)
                p.add_argument("--max-bytes", type=int, default=4096)
                p.add_argument("--wait", type=float, default=0.0)
            if name == "reconcile":
                p.add_argument("--release-lock", action="store_true", required=True)
        elif name == "catalog":
            children = p.add_subparsers(dest="action", required=True)
            for action in ("add", "sync", "list"):
                child = children.add_parser(action)
                child.add_argument("--json", action="store_true")
                if action != "list":
                    child.add_argument("name")
                if action == "add":
                    child.add_argument("source")
                    child.add_argument("--kind", choices=["local", "git"], default="local")
                    child.add_argument("--revision")
        elif name == "capture":
            children = p.add_subparsers(dest="action", required=True)
            child = children.add_parser("init")
            child.add_argument("name")
            child.add_argument("--output")
            child.add_argument("--json", action="store_true")
        elif name == "validate":
            p.add_argument("directory")
            p.add_argument("--schema-only", action="store_true")
            p.add_argument("--profile", default="default")
            p.add_argument("--environment")
        elif name == "profile":
            children = p.add_subparsers(dest="action", required=True)
            child = children.add_parser("create")
            child.add_argument("name")
            child.add_argument("--environment", action="append", required=True)
            child.add_argument("--env", action="append", default=[])
            child.add_argument("--credential", action="append", default=[])
            child.add_argument("--json", action="store_true")
        elif name == "setup":
            p.add_argument("host", choices=["codex", "claude"])
            p.add_argument("--project", default=".")
            p.add_argument("--apply", action="store_true")
        elif name == "cleanup":
            p.add_argument("--days", type=int)
        elif name in {"stats", "dashboard"}:
            p.add_argument("--days", type=int, default=30)
            if name == "dashboard":
                p.add_argument("--output")
            else:
                children = p.add_subparsers(dest="action")
                child = children.add_parser("baseline")
                child.add_argument("ref")
                child.add_argument("--manual-tokens", type=int, required=True)
                child.add_argument("--owlmatic-tokens", type=int, required=True)
                child.add_argument("--setup-tokens", type=int, default=0)
                child.add_argument("--basis", choices=["estimate", "benchmark"], default="estimate")
                child.add_argument("--source", required=True)
                child.add_argument("--sample-size", type=int, default=0)
                child.add_argument("--json", action="store_true")
        elif name == "mcp":
            children = p.add_subparsers(dest="action", required=True)
            children.add_parser("serve")
    return root
