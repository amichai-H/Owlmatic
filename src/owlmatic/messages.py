"""Public results for catalog management, authoring, and installation."""

from pathlib import Path
from typing import Literal

from .domain import Catalog, Contract, Outcome, State


class CatalogReport(Contract):
    catalog: str
    workflows: int
    revision: str | None = None


class CatalogList(Contract):
    catalogs: tuple[Catalog, ...]


class TrustReport(Contract):
    ref: str
    profile: str
    trusted: bool


class DraftReport(Contract):
    draft: Path
    state: Literal["draft"] = "draft"
    example_only: bool = True
    next_step: str


class FixtureReport(Contract):
    run_id: str
    expected: Outcome
    actual: Outcome
    state: State
    matched: bool


class ValidationReport(Contract):
    ref: str
    valid: bool
    tests: tuple[FixtureReport, ...] = ()
    trusted: bool = False


class ProfileReport(Contract):
    profile: str
    environments: tuple[str, ...]


class SetupReport(Contract):
    host: str
    skill: Path
    installed: bool
    mcp_command: tuple[str, ...]
    note: str


class CleanupReport(Contract):
    removed_runs: int
    retention_days: int


class ReconcileReport(Contract):
    run_id: str
    lock_released: bool


class RuntimeReport(Contract):
    python: str
    platform: str
    data_home: Path
    sqlite_fts5: bool
    codex_available: bool
    claude_available: bool
