"""Best-effort completion watching; export failures cannot fail an invocation."""

from contextlib import suppress
from dataclasses import dataclass

from .dashboard_service import DashboardService
from .export_ports import ExportStore, ExportWorkerLauncher
from .export_service import ExportService
from .failures import public_failure
from .ports import Clock, RunRepository
from .statistics import StatisticsRequest


@dataclass(frozen=True)
class AutomaticExport:
    store: ExportStore
    launcher: ExportWorkerLauncher

    def observe(self, run_id: str, timeout: int) -> None:
        try:
            config = self.store.configuration()
            if config.export.mode == "after_workflow" or config.dashboard.local.enabled:
                token_env = (
                    config.export.destination.auth.token_env
                    if config.export.mode == "after_workflow" and config.export.destination
                    else None
                )
                self.launcher.start(run_id, timeout + 120, token_env)
        except Exception as error:
            with suppress(Exception):
                self.store.diagnostic(public_failure(error))


@dataclass(frozen=True)
class ExportWatcher:
    runs: RunRepository
    exports: ExportService
    clock: Clock
    dashboard: DashboardService

    def watch(self, run_id: str, timeout: int) -> None:
        deadline = self.clock.monotonic() + timeout
        while self.runs.get(run_id).state == "running":
            if self.clock.monotonic() >= deadline:
                return
            config = self.exports.store.configuration()
            if config.export.mode != "after_workflow" and not config.dashboard.local.enabled:
                return
            self.clock.sleep(1)
        config = self.exports.store.configuration()
        if config.dashboard.local.enabled:
            try:
                self.dashboard.generate(StatisticsRequest(days=config.export.window_days))
            except Exception as error:
                with suppress(Exception):
                    self.exports.store.diagnostic(public_failure(error))
        while True:
            result = self.exports.push(automatic=True)
            if result.retry_after_seconds <= 0 or self.clock.monotonic() >= deadline:
                return
            self.clock.sleep(min(result.retry_after_seconds, max(0, deadline - self.clock.monotonic())))
