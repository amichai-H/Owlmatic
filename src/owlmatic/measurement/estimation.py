"""Pure conservative comparisons; never infer saved tokens from internal processing."""

from .contracts import Assessment, CostCoverage, TaskObservation


def assess(
    ref: str,
    tasks: tuple[TaskObservation, ...],
    baselines: tuple[TaskObservation, ...],
    coverage: CostCoverage,
) -> Assessment:
    related = tuple(t for t in tasks if t.workflow_ref == ref)
    reuse = tuple(t for t in related if t.purpose == "reuse")
    issues: set[str] = set()
    measured = tuple(t for t in reuse if t.complete and t.usage is not None)
    actual = sum(t.usage.total for t in measured if t.usage) if measured else None
    operational = 0
    context_bytes = 0
    context_tokens = 0
    comparable = bool(reuse)
    context_known = bool(reuse)
    reference_known = bool(reuse)
    used: set[str] = set()
    for task in reuse:
        candidates = tuple(
            b
            for b in baselines
            if b.purpose == "manual"
            and b.complete
            and b.verified
            and b.model
            and (b.host, b.model, b.workload) == (task.host, task.model, task.workload)
            and b.usage is not None
        )
        if not task.complete or task.usage is None or not candidates:
            comparable = False
            context_known = False
            reference_known = False
            issues.add("missing_complete_matching_usage_or_baseline")
            continue
        used.update(b.task_id for b in candidates)
        # Costs always count; only a verified outcome earns baseline credit.
        credit = min(b.usage.total for b in candidates if b.usage) if task.verified else 0
        operational += credit - task.usage.total
        if not task.exposure.complete or not all(b.exposure.complete for b in candidates):
            context_known = False
            reference_known = False
            issues.add("visible_context_incomplete")
        context_bytes += (
            min(b.exposure.visible_bytes for b in candidates) if task.verified else 0
        ) - task.exposure.visible_bytes
        compatible = tuple(
            b
            for b in candidates
            if b.exposure.counter == task.exposure.counter and b.exposure.reference_tokens is not None
        )
        if task.exposure.reference_tokens is None or len(compatible) != len(candidates):
            reference_known = False
        else:
            context_tokens += (
                min(
                    b.exposure.reference_tokens for b in compatible if b.exposure.reference_tokens is not None
                )
                if task.verified
                else 0
            ) - task.exposure.reference_tokens
    if len(measured) != len(reuse):
        issues.add("execution_usage_incomplete")
    costs: list[int | None] = []
    for purpose, complete in (
        ("creation", coverage.setup_complete),
        ("maintenance", coverage.maintenance_complete),
    ):
        observations = tuple(t for t in related if t.purpose == purpose)
        known = complete and all(t.complete and t.usage is not None for t in observations)
        costs.append(sum(t.usage.total for t in observations if t.usage) if known else None)
        if not known:
            issues.add(purpose + "_cost_coverage_unknown")
    creation, maintenance = costs
    net = (
        operational - creation - maintenance
        if comparable and creation is not None and maintenance is not None
        else None
    )
    issues.add("historical_minimum_is_not_a_future_guarantee")
    return Assessment(
        workflow_ref=ref,
        observed_tasks=len(reuse),
        measured_tasks=len(measured),
        baseline_samples=len(used),
        baseline_min_tokens=min(
            (b.usage.total for b in baselines if b.task_id in used and b.usage), default=None
        ),
        baseline_max_tokens=max(
            (b.usage.total for b in baselines if b.task_id in used and b.usage), default=None
        ),
        host_models=tuple(sorted({f"{t.host}/{t.model}" for t in reuse if t.model})),
        workloads=tuple(sorted({t.workload for t in reuse})),
        measured_agent_tokens=actual,
        estimated_operational_savings=operational if comparable else None,
        estimated_net_savings=net,
        estimated_context_reference_tokens=context_tokens if reference_known else None,
        context_bytes_reduced=context_bytes if context_known else None,
        creation_tokens=creation,
        maintenance_tokens=maintenance,
        quality="unmeasured" if not measured else "preliminary" if comparable else "partial",
        issues=tuple(sorted(issues)),
        latest_observation_at=max((t.observed_at for t in related), default=None),
    )
