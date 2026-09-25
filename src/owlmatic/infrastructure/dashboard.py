"""Private, atomic HTML output. No listener or browser-launch side effects."""

from pathlib import Path

from ..errors import OwlError
from ..presentation.dashboard import render_dashboard
from ..serialization import atomic_text
from ..statistics import StatisticsReport
from .files import private_directory


class FileDashboard:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, report: StatisticsReport, output: Path | None) -> Path:
        path = output if output is not None else private_directory(self.root / "reports") / "dashboard.html"
        path = path.expanduser().absolute()
        if path.suffix.lower() != ".html" or path.is_symlink():
            raise OwlError("INVALID_REPORT_PATH", "Choose a regular .html output path")
        atomic_text(path, render_dashboard(report))
        return path
