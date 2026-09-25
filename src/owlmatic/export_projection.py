"""Explicit allowlist: adding a local statistic never implicitly exports it."""

from .export_contracts import (
    DailyMetrics,
    ExportScope,
    SavingsMetrics,
    Snapshot,
    StatisticsSnapshot,
    StatisticsSnapshotV2,
    UsageMetrics,
    WorkflowMetrics,
)
from .measurement_wire import MeasurementMetrics
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
    report: StatisticsReport,
    scope: ExportScope,
    source_id: str,
    snapshot_id: str,
    sequence: int,
    *,
    version: str = "1",
    source_label: str | None = None,
    include_measurements: bool = False,
) -> Snapshot:
    value = StatisticsSnapshot(
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
    if version == "1":
        return value
    return StatisticsSnapshotV2(
        snapshot_id=value.snapshot_id,
        source_id=value.source_id,
        sequence=value.sequence,
        generated_at=value.generated_at,
        since=value.since,
        days=value.days,
        shared=value.shared,
        usage=value.usage,
        savings=value.savings,
        daily=value.daily,
        workflows=value.workflows,
        source_label=source_label,
        measurements_shared=include_measurements,
        measurements=measurement_metrics(report, scope.workflow_identifiers)
        if include_measurements
        else None,
    )


def measurement_metrics(report: StatisticsReport, identities: bool) -> MeasurementMetrics:
    measurement = report.measurements
    rows = measurement.assessments if measurement else ()
    actual = [row.measured_agent_tokens for row in rows if row.measured_agent_tokens is not None]
    operational = [row.estimated_operational_savings for row in rows]
    net = [row.estimated_net_savings for row in rows]
    context = [row.estimated_context_reference_tokens for row in rows]
    byte_counts = [row.context_bytes_reduced for row in rows]
    attributed = measurement is not None and measurement.unattributed_tasks == 0
    return MeasurementMetrics(
        observed_tasks=sum(row.observed_tasks for row in rows),
        measured_tasks=sum(row.measured_tasks for row in rows),
        unattributed_tasks=measurement.unattributed_tasks if measurement else 0,
        measured_agent_tokens=sum(actual) if actual else None,
        estimated_operational_savings=sum(x for x in operational if x is not None)
        if rows and attributed and None not in operational
        else None,
        estimated_net_savings=sum(x for x in net if x is not None)
        if rows and attributed and None not in net
        else None,
        estimated_context_reference_tokens=sum(x for x in context if x is not None)
        if rows and attributed and None not in context
        else None,
        context_bytes_reduced=sum(x for x in byte_counts if x is not None)
        if rows and attributed and None not in byte_counts
        else None,
        latest_observation_at=measurement.latest_observation_at if measurement else None,
        workflows=rows if identities else None,
    )
