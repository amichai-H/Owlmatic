"""Conservative comparisons never turn missing data or failure into savings."""

import pytest

from owlmatic.measurement.contracts import CostCoverage, Exposure, TaskObservation, TokenUsage
from owlmatic.measurement.estimation import assess


def task(name: str, tokens: int, **changes: object) -> TaskObservation:
    return TaskObservation.model_validate(
        {
            "task_id": name,
            "host": "codex",
            "model": "model",
            "purpose": "reuse",
            "workload": "small",
            "workflow_ref": "catalog/workflow@hash",
            "verified": True,
            "complete": True,
            "usage": TokenUsage(input_tokens=tokens, output_tokens=0),
            "exposure": Exposure(
                complete=True,
                visible_bytes=tokens * 3,
                reference_tokens=tokens,
                counter="cl100k_base_reference",
                tool_results=1,
            ),
            "source_digest": name,
            "source_key": name,
            "observed_at": "2026-09-25T12:00:00+00:00",
            **changes,
        }
    )


REF = "catalog/workflow@hash"
KNOWN = CostCoverage(workflow_ref=REF, setup_complete=True, maintenance_complete=True)


def test_costs_and_negative_findings_are_accounted_once() -> None:
    runs = (
        task("good", 100),
        task("bad", 80, verified=False),
        task("create", 500, purpose="creation"),
        task("repair", 20, purpose="maintenance"),
    )
    baselines = (task("b1", 1000, purpose="manual"), task("b2", 800, purpose="manual"))
    result = assess(REF, runs, baselines, KNOWN)
    assert result.measured_agent_tokens == 180
    assert result.estimated_operational_savings == 620  # 800 credit - both attempts
    assert result.estimated_net_savings == 100
    assert result.baseline_min_tokens == 800 and result.baseline_max_tokens == 1000


def test_negative_savings_are_not_clamped() -> None:
    result = assess(REF, (task("run", 1200),), (task("base", 1000, purpose="manual"),), KNOWN)
    assert result.estimated_net_savings == -200


def test_unknown_creation_cost_prevents_net_claim() -> None:
    result = assess(
        REF, (task("run", 100),), (task("base", 1000, purpose="manual"),), CostCoverage(workflow_ref=REF)
    )
    assert result.estimated_operational_savings == 900
    assert result.estimated_net_savings is None and result.creation_tokens is None


@pytest.mark.parametrize(
    "changes",
    [{"model": "other"}, {"host": "claude"}, {"workload": "large"}, {"complete": False}, {"verified": False}],
)
def test_incompatible_or_invalid_baselines_do_not_earn_credit(changes: dict[str, object]) -> None:
    result = assess(REF, (task("run", 100),), (task("base", 1000, purpose="manual", **changes),), KNOWN)
    assert result.estimated_operational_savings is None


def test_usage_without_visible_tool_history_does_not_invent_context_savings() -> None:
    run = task("run", 100, exposure=Exposure(visible_bytes=0, tool_results=0))
    result = assess(REF, (run,), (task("base", 1000, purpose="manual"),), KNOWN)
    assert result.estimated_operational_savings == 900
    assert result.context_bytes_reduced is None and result.estimated_context_reference_tokens is None


def test_no_context_replay_multiplier_or_double_counted_reasoning() -> None:
    result = assess(REF, (task("run", 100),), (task("base", 1000, purpose="manual"),), KNOWN)
    assert result.estimated_context_reference_tokens == 900
    assert result.context_bytes_reduced == 2700


def test_new_workflow_digest_does_not_inherit_creation_coverage_or_costs() -> None:
    result = assess(
        "catalog/workflow@new", (task("old", 100),), (), CostCoverage(workflow_ref="catalog/workflow@new")
    )
    assert result.observed_tasks == 0 and result.estimated_net_savings is None
