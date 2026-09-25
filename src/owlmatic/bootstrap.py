"""Composition root: wire application ports to infrastructure implementations."""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .administration import Administration
from .authoring_service import AuthoringService
from .automatic_export import AutomaticExport
from .catalog_service import CatalogService
from .dashboard_service import DashboardService
from .domain import Catalog
from .execution_service import ExecutionService
from .export_service import ExportService
from .infrastructure.authoring import FileDrafts, FileIntegrations, resource
from .infrastructure.bundles import FileBundles
from .infrastructure.dashboard import FileDashboard
from .infrastructure.export_launcher import SubprocessExportLauncher
from .infrastructure.export_store import FileExportStore
from .infrastructure.files import FileArtifacts, FileSettings
from .infrastructure.git import FileCatalogSource
from .infrastructure.http_export import HttpSnapshotSender
from .infrastructure.process import SubprocessWorkflow
from .infrastructure.run_guard import FileRunGuard
from .infrastructure.run_repository import SqliteRuns
from .infrastructure.schema import JsonSchemaValidator
from .infrastructure.sqlite import SqliteDatabase, SqliteWorkflows
from .infrastructure.statistics import SqliteStatistics
from .infrastructure.system import SubprocessLauncher, SystemClock, SystemCommands, SystemHost
from .messages import CatalogReport, RuntimeReport
from .policy import ExecutionPolicy
from .recovery_service import RecoveryService
from .statistics_service import StatisticsService
from .supervisor import Supervisor


@dataclass(frozen=True)
class Application:
    catalog: CatalogService
    execution: ExecutionService
    authoring: AuthoringService
    administration: Administration
    supervisor: Supervisor
    diagnostics: Callable[[], RuntimeReport]
    examples: Callable[[], CatalogReport]
    recovery: RecoveryService
    statistics: StatisticsService
    exports: ExportService
    dashboard: DashboardService


def create_application(root: Path | None = None) -> Application:
    root = (
        root or Path(os.environ.get("OWLMATIC_HOME", str(Path.home() / ".owlmatic"))).expanduser().resolve()
    )
    settings = FileSettings(root)
    clock = SystemClock()
    schemas = JsonSchemaValidator()
    bundles = FileBundles(root, schemas)
    database = SqliteDatabase(root)
    workflows = SqliteWorkflows(database)
    runs = SqliteRuns(database, clock)
    commands = SystemCommands()
    host = SystemHost(commands)
    artifacts = FileArtifacts(root)
    policy = ExecutionPolicy(settings, bundles, schemas, host)
    catalog = CatalogService(settings, workflows, FileCatalogSource(root, bundles, commands), host)
    guard = FileRunGuard(root)
    recovery = RecoveryService(runs, artifacts, guard)
    statistics = StatisticsService(SqliteStatistics(database), workflows, clock)
    export_store = FileExportStore(root)
    exports = ExportService(statistics, export_store, HttpSnapshotSender(), clock, lambda: uuid.uuid4().hex)
    execution = ExecutionService(
        workflows,
        runs,
        artifacts,
        SubprocessLauncher(root),
        clock,
        policy,
        lambda: "run_" + uuid.uuid4().hex,
        recovery,
        AutomaticExport(export_store, SubprocessExportLauncher(root)),
    )
    supervisor = Supervisor(
        policy,
        artifacts,
        runs,
        SubprocessWorkflow(clock, guard),
        clock,
        guard,
        recovery,
        lambda: uuid.uuid4().hex,
    )
    return Application(
        catalog,
        execution,
        AuthoringService(bundles, workflows, execution, FileDrafts(root)),
        Administration(settings, FileIntegrations()),
        supervisor,
        lambda: RuntimeReport(
            python=sys.version.split()[0],
            platform=sys.platform,
            data_home=root,
            sqlite_fts5=True,
            codex_available=shutil.which("codex") is not None,
            claude_available=shutil.which("claude") is not None,
        ),
        lambda: catalog.add(Catalog(name="examples", source=str(resource("examples")))),
        recovery,
        statistics,
        exports,
        DashboardService(statistics, FileDashboard(root), export_store),
    )
