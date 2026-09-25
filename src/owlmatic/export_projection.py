"""Explicit allowlist: adding a local statistic never implicitly exports it."""

from .export_contracts import (
    DailyMetrics,
    ExportScope,
    SavingsMetrics,
    StatisticsSnapshot,
    UsageMetrics,
    WorkflowMetrics,
)
from .statistics import StatisticsReport, WorkflowStatistics


def usage(report: StatisticsReport | WorkflowStatistics) -> UsageMetrics:
    return UsageMetrics(
        runs=report.runs,
        terminal=report.terminal,
        verified=report.verified,
        execution_errors=report.execution_errors,
        running=report.running,
        duration_ms=report.duration_ms,
    )


def savings(report: StatisticsReport | WorkflowStatistics) -> SavingsMetrics:
    if isinstance(report, StatisticsReport):
        modeled, setup = report.modeled_runs, report.estimated_setup_tokens
    else:
        modeled = report.terminal if report.estimated_net_tokens_saved is not None else 0
        setup = report.baseline.setup_tokens if report.baseline and modeled else None
    return SavingsMetrics(
        modeled_runs=modeled,
        manual_tokens=report.estimated_manual_tokens,
        owlmatic_tokens=report.estimated_owlmatic_tokens,
        setup_tokens=setup,
        net_tokens_saved=report.estimated_net_tokens_saved,
    )


def snapshot(
    report: StatisticsReport, scope: ExportScope, source_id: str, snapshot_id: str, sequence: int
) -> StatisticsSnapshot:
    return StatisticsSnapshot(
        snapshot_id=snapshot_id,
        source_id=source_id,
        sequence=sequence,
        generated_at=report.generated_at,
        since=report.since,
        days=report.days,
        shared=scope,
        usage=usage(report),
        savings=savings(report) if scope.savings_estimates else None,
        daily=tuple(
            DailyMetrics(
                day=d.day,
                runs=d.runs,
                verified=d.verified,
                estimated_tokens_saved_before_setup=d.estimated_tokens_saved_before_setup
                if scope.savings_estimates
                else None,
            )
            for d in report.daily
        )
        if scope.daily_breakdown
        else None,
        workflows=tuple(
            WorkflowMetrics(
                ref=w.ref, usage=usage(w), savings=savings(w) if scope.savings_estimates else None
            )
            for w in report.workflows
        )
        if scope.workflow_identifiers
        else None,
    )
