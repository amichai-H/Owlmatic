"""Dependency direction is a project contract, enforced independently of behavior."""

import ast

from tests.conftest import ROOT


def test_application_modules_do_not_import_infrastructure_or_transports() -> None:
    application = {
        "catalog_service",
        "execution_service",
        "authoring_service",
        "administration",
        "policy",
        "supervisor",
        "verification",
        "recovery_service",
        "statistics_service",
        "export_service",
        "export_projection",
        "automatic_export",
        "dashboard_service",
    }
    forbidden = {
        "infrastructure",
        "cli",
        "mcp_server",
        "bootstrap",
        "subprocess",
        "sqlite3",
        "mcp",
        "argparse",
    }
    for name in application:
        path = ROOT / "src/owlmatic" / f"{name}.py"
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(set(module.split(".")) & forbidden for module in modules), (
                f"Boundary violation in {name}"
            )


def test_core_has_no_any_or_unchecked_contract_updates() -> None:
    for path in (ROOT / "src/owlmatic").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Name) and node.id == "Any"), str(path)
            assert not (isinstance(node, ast.Attribute) and node.attr in {"model_copy", "model_construct"}), (
                str(path)
            )
