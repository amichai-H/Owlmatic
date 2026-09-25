"""Conservative savings model over retained invocations, independent of transports."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .ports import Clock, WorkflowRepository
from .statistics import (
    DailyStatistics,
    RunBucket,
    SavingsBaseline,
    StatisticsReport,
    StatisticsRequest,
    WorkflowStatistics,
)
from .statistics_ports import StatisticsRepository


def verified(bucket: RunBucket) -> bool:
    # A verified negative finding is useful work too. Unknown effects and
    # inconclusive results cannot establish that the manual task was replaced.
    return (
        bucket.state == "completed"
        and bucket.outcome in {"pass", "fail"}
        and bucket.effects in {"none", "completed"}
    )


def workflow_statistics(
    ref: str, buckets: Sequence[RunBucket], baseline: SavingsBaseline | None
) -> WorkflowStatistics:
    total = sum(b.count for b in buckets)
    running = sum(b.count for b in buckets if b.state == "running")
    completed = sum(b.count for b in buckets if verified(b))
    terminal = total - running
    manual = completed * baseline.manual_tokens if baseline and terminal else None
    automated = terminal * baseline.owlmatic_tokens if baseline and terminal else None
    net = (
        manual - automated - baseline.setup_tokens
        if baseline is not None and manual is not None and automated is not None
        else None
    )
    return WorkflowStatistics(
        ref=ref,
        runs=total,
        terminal=terminal,
        verified=completed,
        passed=sum(b.count for b in buckets if verified(b) and b.outcome == "pass"),
        detected_failures=sum(b.count for b in buckets if verified(b) and b.outcome == "fail"),
        execution_errors=sum(b.count for b in buckets if b.state in {"error", "blocked", "cancelled"}),
        running=running,
        duration_ms=sum(b.duration_ms for b in buckets if b.state != "running"),
        baseline=baseline,
        estimated_manual_tokens=manual,
        estimated_owlmatic_tokens=automated,
        estimated_net_tokens_saved=net,
    )


@dataclass(frozen=True)
class StatisticsService:
    repository: StatisticsRepository
    workflows: WorkflowRepository
    clock: Clock

    def baseline(self, baseline: SavingsBaseline) -> SavingsBaseline:
        self.workflows.get(baseline.ref)  # Only exact, registered versions have baselines.
        self.repository.save_baseline(baseline)
        return baseline

    def report(self, request: StatisticsRequest) -> StatisticsReport:
        now = datetime.fromtimestamp(self.clock.epoch(), UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=request.days - 1)
        buckets = self.repository.buckets(start.isoformat(), now.isoformat())
        baselines = {b.ref: b for b in self.repository.baselines()}
        by_workflow: dict[str, list[RunBucket]] = defaultdict(list)
        by_day: dict[date, list[RunBucket]] = defaultdict(list)
        for bucket in buckets:
            if bucket.purpose == "workflow":
                by_workflow[bucket.ref].append(bucket)
                by_day[bucket.day].append(bucket)
        workflows = tuple(
            workflow_statistics(ref, rows, baselines.get(ref)) for ref, rows in sorted(by_workflow.items())
        )
        modeled = tuple(w for w in workflows if w.estimated_net_tokens_saved is not None)
        daily: list[DailyStatistics] = []
        for offset in range(request.days):
            day = start.date() + timedelta(days=offset)
            rows = by_day[day]
            cost = 0
            for bucket in rows:
                baseline = baselines.get(bucket.ref)
                if baseline and bucket.state != "running":
                    cost += bucket.count * (
                        (baseline.manual_tokens if verified(bucket) else 0) - baseline.owlmatic_tokens
                    )
            daily.append(
                DailyStatistics(
                    day=day,
                    runs=sum(b.count for b in rows),
                    verified=sum(b.count for b in rows if verified(b)),
                    estimated_tokens_saved_before_setup=cost if modeled else None,
                )
            )
        return StatisticsReport(
            generated_at=now.isoformat(),
            since=start.isoformat(),
            days=request.days,
            runs=sum(w.runs for w in workflows),
            terminal=sum(w.terminal for w in workflows),
            verified=sum(w.verified for w in workflows),
            execution_errors=sum(w.execution_errors for w in workflows),
            running=sum(w.running for w in workflows),
            duration_ms=sum(w.duration_ms for w in workflows),
            excluded_validation_runs=sum(b.count for b in buckets if b.purpose == "validation"),
            excluded_legacy_runs=sum(b.count for b in buckets if b.purpose == "unknown"),
            modeled_runs=sum(w.terminal for w in modeled),
            estimated_manual_tokens=sum(w.estimated_manual_tokens or 0 for w in modeled) if modeled else None,
            estimated_owlmatic_tokens=sum(w.estimated_owlmatic_tokens or 0 for w in modeled)
            if modeled
            else None,
            estimated_setup_tokens=sum(w.baseline.setup_tokens for w in modeled if w.baseline)
            if modeled
            else None,
            estimated_net_tokens_saved=sum(w.estimated_net_tokens_saved or 0 for w in modeled)
            if modeled
            else None,
            workflows=workflows,
            daily=tuple(daily),
        )
