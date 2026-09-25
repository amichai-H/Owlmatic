"""Optional local viewer, independent of statistics calculation and remote export."""

from dataclasses import dataclass
from pathlib import Path

from .export_ports import ExportStore, StatisticsSource
from .statistics import DashboardReport, StatisticsRequest
from .statistics_ports import DashboardWriter


@dataclass(frozen=True)
class DashboardService:
    statistics: StatisticsSource
    writer: DashboardWriter
    configuration: ExportStore

    def generate(self, request: StatisticsRequest, output: Path | None = None) -> DashboardReport:
        # The explicit command opts into generating this local artifact, even when
        # automatic local reporting is disabled in configuration.
        report = self.statistics.report(request)
        path = self.writer.write(report, output)
        return DashboardReport(path=path, generated_at=report.generated_at, days=report.days)
