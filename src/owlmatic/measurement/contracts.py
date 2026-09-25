"""Immutable accounting records. Absence is not zero; estimates are not telemetry."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from ..domain import Contract

HostName = Literal["codex", "claude"]
Purpose = Literal["manual", "reuse", "creation", "maintenance"]


class TokenUsage(Contract):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def subsets(self) -> Self:
        if (self.cached_input_tokens or 0) + (self.cache_write_input_tokens or 0) > self.input_tokens:
            raise ValueError("Cache categories cannot exceed total input")
        if (self.reasoning_output_tokens or 0) > self.output_tokens:
            raise ValueError("Reasoning is a subset of output")
        return self

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class Exposure(Contract):
    complete: bool = False
    visible_bytes: int = Field(ge=0)
    reference_tokens: int | None = Field(default=None, ge=0)
    counter: Literal["bytes", "cl100k_base_reference"] = "bytes"
    tool_results: int = Field(ge=0)


class TaskObservation(Contract):
    task_id: str = Field(min_length=1, max_length=128)
    host: HostName
    model: str | None = Field(default=None, max_length=128)
    purpose: Purpose
    workload: str = Field(min_length=1, max_length=128)
    workflow_ref: str | None = Field(default=None, max_length=512)
    run_ids: tuple[str, ...] = ()
    verified: bool = False
    verification_basis: Literal["unverified", "user_attested", "workflow_evidence"] = "unverified"
    usage: TokenUsage | None = None
    complete: bool = False
    exposure: Exposure
    issues: tuple[str, ...] = ()
    source_digest: str
    source_key: str
    source_file_key: str = ""
    first_line: int = Field(default=1, ge=1)
    last_line: int | None = Field(default=None, ge=1)
    observed_at: str

    def attributed(
        self,
        ref: str | None,
        verified: bool,
        basis: Literal["unverified", "user_attested", "workflow_evidence"],
    ) -> "TaskObservation":
        return TaskObservation(
            task_id=self.task_id,
            host=self.host,
            model=self.model,
            purpose=self.purpose,
            workload=self.workload,
            workflow_ref=ref,
            run_ids=self.run_ids,
            verified=verified,
            verification_basis=basis,
            usage=self.usage,
            complete=self.complete,
            exposure=self.exposure,
            issues=self.issues,
            source_digest=self.source_digest,
            source_key=self.source_key,
            source_file_key=self.source_file_key,
            first_line=self.first_line,
            last_line=self.last_line,
            observed_at=self.observed_at,
        )


class ImportRequest(Contract):
    host: HostName
    session: Path
    task_id: str = Field(min_length=1, max_length=128)
    purpose: Purpose = "reuse"
    workload: str = Field(default="default", min_length=1, max_length=128)
    workflow_ref: str | None = None
    run_ids: tuple[str, ...] = ()
    model: str | None = None
    verified: bool = False
    first_line: int = Field(default=1, ge=1)
    last_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def line_range(self) -> Self:
        if self.last_line is not None and self.last_line < self.first_line:
            raise ValueError("Invalid line range")
        if len(set(self.run_ids)) != len(self.run_ids):
            raise ValueError("Duplicate run IDs")
        return self


class BaselineLink(Contract):
    workflow_ref: str
    task_id: str


class CostCoverage(Contract):
    workflow_ref: str
    setup_complete: bool = False
    maintenance_complete: bool = False


class Assessment(Contract):
    workflow_ref: str
    method: Literal["observed-history-v1"] = "observed-history-v1"
    observed_tasks: int
    measured_tasks: int
    baseline_samples: int
    baseline_min_tokens: int | None = None
    baseline_max_tokens: int | None = None
    host_models: tuple[str, ...] = ()
    workloads: tuple[str, ...] = ()
    measured_agent_tokens: int | None = None
    estimated_operational_savings: int | None = None
    estimated_net_savings: int | None = None
    estimated_context_reference_tokens: int | None = None
    context_bytes_reduced: int | None = None
    creation_tokens: int | None = None
    maintenance_tokens: int | None = None
    quality: Literal["unmeasured", "partial", "preliminary"]
    issues: tuple[str, ...]
    latest_observation_at: str | None = None
    comparison: Literal["recorded_manual_tasks"] = "recorded_manual_tasks"


class MeasurementReport(Contract):
    assessments: tuple[Assessment, ...]
    unattributed_tasks: int
    latest_observation_at: str | None = None
    scope: Literal["retained_measurement_history"] = "retained_measurement_history"


class ImportResult(Contract):
    observation: TaskObservation
    changed: bool


class WatchResult(Contract):
    imported: int
    failures: tuple[str, ...]


class Emission(Contract):
    operation: str
    run_id: str | None
    emitted_at: str
    exposure: Exposure
    delivery: Literal["emitted_not_confirmed_visible"] = "emitted_not_confirmed_visible"


class MeasurementConfiguration(Contract):
    enabled: bool = False
    sources: tuple[ImportRequest, ...] = Field(default=(), max_length=100)
    poll_seconds: int = Field(default=5, ge=1, le=300)
    retention_days: int = Field(default=90, ge=1, le=3650)
