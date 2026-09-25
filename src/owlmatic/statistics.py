"""Typed statistics contracts. Token savings are modeled, never provider telemetry."""

from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from .domain import Contract, Effects, Outcome, State
from .measurement.contracts import MeasurementReport

RunPurpose = Literal["workflow", "validation", "unknown"]


class SavingsBaseline(Contract):
    ref: str = Field(min_length=1, max_length=512)
    manual_tokens: int = Field(ge=1, le=1_000_000_000)
    owlmatic_tokens: int = Field(ge=0, le=1_000_000_000)
    setup_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    basis: Literal["estimate", "benchmark"] = "estimate"
    source: str = Field(min_length=1, max_length=512)
    sample_size: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def benchmark_has_samples(self) -> Self:
        if self.basis == "benchmark" and self.sample_size == 0:
            raise ValueError("A benchmark baseline requires a sample size")
        return self


class StatisticsRequest(Contract):
    days: int = Field(default=30, ge=1, le=3650)


class RunBucket(Contract):
    day: date
    ref: str
    purpose: RunPurpose
    state: State
    outcome: Outcome
    effects: Effects
    count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class WorkflowStatistics(Contract):
    ref: str
    runs: int
    terminal: int
    verified: int
    passed: int
    detected_failures: int
    execution_errors: int
    running: int
    duration_ms: int
    baseline: SavingsBaseline | None
    estimated_manual_tokens: int | None
    estimated_owlmatic_tokens: int | None
    estimated_net_tokens_saved: int | None


class DailyStatistics(Contract):
    day: date
    runs: int
    verified: int
    estimated_tokens_saved_before_setup: int | None


class StatisticsReport(Contract):
    measurements: MeasurementReport | None = None
    generated_at: str
    since: str
    days: int
    scope: Literal["retained_local_runs"] = "retained_local_runs"
    runs: int
    terminal: int
    verified: int
    execution_errors: int
    running: int
    duration_ms: int
    excluded_validation_runs: int
    excluded_legacy_runs: int
    modeled_runs: int
    estimated_manual_tokens: int | None
    estimated_owlmatic_tokens: int | None
    estimated_setup_tokens: int | None
    estimated_net_tokens_saved: int | None
    workflows: tuple[WorkflowStatistics, ...]
    daily: tuple[DailyStatistics, ...]
    provider_tokens: int | None = None


class DashboardReport(Contract):
    path: Path
    generated_at: str
    days: int
