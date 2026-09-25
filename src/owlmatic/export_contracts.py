"""Public v1 snapshot contract. Only explicitly selected statistics cross this boundary."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .domain import Contract

Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Count = Annotated[int, Field(ge=0)]


class ExportScope(Contract):
    usage_totals: Literal[True] = True
    savings_estimates: bool = False
    daily_breakdown: bool = False
    workflow_identifiers: bool = False


class UsageMetrics(Contract):
    runs: Count
    terminal: Count
    verified: Count
    execution_errors: Count
    running: Count
    duration_ms: Count


class SavingsMetrics(Contract):
    kind: Literal["estimate"] = "estimate"
    modeled_runs: Count
    manual_tokens: Count | None = None
    owlmatic_tokens: Count | None = None
    setup_tokens: Count | None = None
    net_tokens_saved: int | None = None


class DailyMetrics(Contract):
    day: date
    runs: Count
    verified: Count
    estimated_tokens_saved_before_setup: int | None = None


class WorkflowMetrics(Contract):
    ref: str = Field(max_length=512)
    usage: UsageMetrics
    savings: SavingsMetrics | None = None


class StatisticsSnapshot(Contract):
    schema_version: Literal["1"] = "1"
    kind: Literal["owlmatic.statistics.snapshot"] = "owlmatic.statistics.snapshot"
    snapshot_id: Identifier
    source_id: Identifier
    sequence: int = Field(ge=1)
    generated_at: str = Field(max_length=64)
    since: str = Field(max_length=64)
    days: int = Field(ge=1, le=3650)
    scope: Literal["retained_local_runs"] = "retained_local_runs"
    shared: ExportScope
    usage: UsageMetrics
    savings: SavingsMetrics | None = None
    daily: tuple[DailyMetrics, ...] | None = Field(default=None, max_length=3650)
    workflows: tuple[WorkflowMetrics, ...] | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def enforce_scope(self) -> Self:
        if (self.savings is not None) != self.shared.savings_estimates:
            raise ValueError("Savings payload must match sharing scope")
        if (self.daily is not None) != self.shared.daily_breakdown:
            raise ValueError("Daily payload must match sharing scope")
        if (self.workflows is not None) != self.shared.workflow_identifiers:
            raise ValueError("Workflow payload must match sharing scope")
        if not self.shared.savings_estimates:
            if any(row.estimated_tokens_saved_before_setup is not None for row in self.daily or ()):
                raise ValueError("Daily savings require savings consent")
            if any(row.savings is not None for row in self.workflows or ()):
                raise ValueError("Workflow savings require savings consent")
        return self
