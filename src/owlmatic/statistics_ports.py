"""Storage and presentation boundaries for statistics."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .statistics import RunBucket, SavingsBaseline, StatisticsReport


class StatisticsRepository(Protocol):
    def buckets(self, since: str, until: str) -> Sequence[RunBucket]: ...
    def baselines(self) -> Sequence[SavingsBaseline]: ...
    def save_baseline(self, baseline: SavingsBaseline) -> None: ...


class DashboardWriter(Protocol):
    def write(self, report: StatisticsReport, output: Path | None) -> Path: ...
