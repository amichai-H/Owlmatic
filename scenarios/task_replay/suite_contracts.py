"""Validated scenario data; executable capabilities are registered in code."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .fixtures import WorkerPlan
from .workflow.reproduction.contracts import Contract, Identifier


class Expected(Contract):
    finding: Literal["reproduced", "not_reproduced", "inconclusive", "blocked"]
    attempts: int = Field(ge=0, le=10)
    reason: str | None = None


class Case(Contract):
    id: Identifier
    amounts_cents: tuple[Annotated[int, Field(gt=0, le=1000000)], ...] = Field(
        default=(100, 250), min_length=1, max_length=100
    )
    revision: int = Field(default=7, ge=0, le=1000000)
    replay_effects: Literal["local_only", "external"] = "local_only"
    snapshot: Literal["complete", "missing_state", "missing_item", "unsupported_task"] = "complete"
    worker: WorkerPlan = Field(default_factory=WorkerPlan)
    max_attempts: int = Field(default=3, ge=1, le=10)
    expected: Expected


class Suite(Contract):
    version: Literal[1] = 1
    recipe: Literal["invoice-replay"] = "invoice-replay"
    cases: tuple[Case, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct_ids(self) -> Self:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Scenario IDs must be unique")
        return self


class CaseReport(Contract):
    id: str
    run_id: str
    finding: str
    attempts: int
    oracle_matched: bool


class SuiteReport(Contract):
    workflow_ref: str
    cases: tuple[CaseReport, ...]
    passed: bool
    provider_token_savings: None = None
    note: str = "Scenario correctness only; no agent-token savings inferred from script work or DB bytes"
