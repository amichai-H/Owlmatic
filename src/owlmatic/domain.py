"""Domain contracts. Dynamic JSON is confined to user schemas and payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .errors import OwlError

JsonObject: TypeAlias = dict[str, JsonValue]
Outcome: TypeAlias = Literal["pass", "fail", "inconclusive", "not_applicable"]
State: TypeAlias = Literal["running", "completed", "blocked", "error", "cancelled"]
Effects: TypeAlias = Literal["none", "completed", "partial", "unknown"]
View: TypeAlias = Literal["status", "failures", "logs", "evidence", "result", "diagnostic"]
Name: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,95}$")]
ShortText: TypeAlias = Annotated[str, Field(min_length=1, max_length=256)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Requirements(Contract):
    platforms: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    repository: str | None = None


class Execution(Contract):
    timeout_seconds: int = Field(default=600, ge=1, le=86400)
    concurrency_input: str | None = None
    remote: bool = False


class Verification(Contract):
    required_checks: tuple[ShortText, ...] = Field(min_length=1, max_length=32)


class Fixture(Contract):
    inputs: JsonObject
    outcome: Outcome


class Manifest(Contract):
    schema_version: Literal[1]
    id: Name
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=240)
    owner: str = Field(min_length=1, max_length=100)
    aliases: tuple[ShortText, ...] = Field(default=(), max_length=32)
    examples: tuple[ShortText, ...] = Field(default=(), max_length=32)
    entrypoint: tuple[ShortText, ...] = Field(min_length=1, max_length=32)
    input_schema: ShortText
    output_schema: ShortText
    requirements: Requirements = Requirements()
    environments: tuple[ShortText, ...] = Field(min_length=1, max_length=32)
    effects: tuple[ShortText, ...] = Field(max_length=32)
    credentials: tuple[ShortText, ...] = Field(default=(), max_length=32)
    execution: Execution = Execution()
    verification: Verification
    tests: tuple[Fixture, ...] = Field(default=(), max_length=32)


class Workflow(Contract):
    ref: str
    catalog: str
    id: str
    digest: str
    directory: Path
    manifest: Manifest
    input_schema: JsonObject
    output_schema: JsonObject
    source_revision: str | None = None


class Catalog(Contract):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    kind: Literal["local", "git"] = "local"
    source: str
    revision: str | None = None
    resolved: str | None = None

    def at_revision(self, resolved: str | None) -> Catalog:
        return Catalog(
            name=self.name, kind=self.kind, source=self.source, revision=self.revision, resolved=resolved
        )


class Profile(Contract):
    environments: tuple[str, ...] = ("local", "integration", "simulation")
    grants: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    credentials: dict[str, str] = Field(default_factory=dict)
    max_timeout_seconds: int = Field(default=600, ge=1, le=86400)
    max_log_bytes: int = Field(default=10485760, ge=1024, le=1073741824)

    def with_grants(self, grants: tuple[str, ...]) -> Profile:
        return Profile(
            environments=self.environments,
            grants=grants,
            env=self.env,
            credentials=self.credentials,
            max_timeout_seconds=self.max_timeout_seconds,
            max_log_bytes=self.max_log_bytes,
        )


class Settings(Contract):
    version: Literal[1] = 1
    catalogs: tuple[Catalog, ...] = ()
    retention_days: int = Field(default=7, ge=0)
    profiles: dict[str, Profile] = Field(default_factory=lambda: {"default": Profile()})

    def with_catalogs(self, catalogs: tuple[Catalog, ...]) -> Settings:
        return Settings(
            version=self.version,
            catalogs=catalogs,
            retention_days=self.retention_days,
            profiles=self.profiles,
        )

    def with_profile(self, name: str, profile: Profile) -> Settings:
        return Settings(
            version=self.version,
            catalogs=self.catalogs,
            retention_days=self.retention_days,
            profiles={**self.profiles, name: profile},
        )


class Check(Contract):
    name: ShortText
    status: Literal["pass", "fail", "skip"]


class Evidence(Contract):
    run_id: str
    outcome: Outcome
    checks: tuple[Check, ...] = Field(max_length=128)
    outputs: JsonObject
    effects: Effects


class CheckCounts(Contract):
    required: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)


class Failure(Contract):
    code: str
    message: str


class Target(Contract):
    environment: str
    workspace: str
    source_revision: str | None = None
    dirty: bool | None = None


class Run(Contract):
    run_id: str
    workflow_ref: str
    profile: str | None = None
    purpose: Literal["workflow", "validation", "unknown"] = "unknown"
    state: State = "running"
    outcome: Outcome = "inconclusive"
    effects: Effects = "none"
    target: Target
    started_at: str
    finished_at: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    evidence_ref: str
    checks: CheckCounts | None = None
    outputs: JsonObject | None = None
    error: Failure | None = None
    log_truncated: bool = False

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        if self.state == "running":
            if self.outcome != "inconclusive" or self.finished_at is not None or self.error is not None:
                raise ValueError("An active run cannot have a result or finish timestamp")
            if self.checks is not None or self.outputs is not None:
                raise ValueError("An active run cannot have final evidence")
        else:
            if not self.finished_at:
                raise ValueError("A terminal run requires a finish timestamp")
            if self.state == "completed":
                if self.error is not None or self.checks is None:
                    raise ValueError("Completed execution requires check counts and no execution error")
                if self.outcome == "pass" and (
                    self.checks.failed
                    or self.checks.passed < self.checks.required
                    or self.effects in {"unknown", "partial"}
                ):
                    raise ValueError(
                        "Passing execution requires complete successful checks and known effects"
                    )
                if self.outcome == "fail" and not self.checks.failed:
                    raise ValueError("Failed verification requires a failed check")
            elif self.outcome != "inconclusive" or self.error is None:
                raise ValueError("Execution failures require an error and inconclusive outcome")
        return self

    def finish(
        self,
        *,
        state: Literal["completed", "blocked", "error", "cancelled"],
        outcome: Outcome,
        effects: Effects,
        finished_at: str,
        duration_ms: int = 0,
        checks: CheckCounts | None = None,
        outputs: JsonObject | None = None,
        error: Failure | None = None,
        log_truncated: bool = False,
    ) -> Run:
        """A typed transition preserves invocation identity and revalidates the record."""
        if self.state != "running":
            raise OwlError("INVALID_TRANSITION", "A terminal run cannot be completed again")
        return Run(
            run_id=self.run_id,
            workflow_ref=self.workflow_ref,
            profile=self.profile,
            purpose=self.purpose,
            state=state,
            outcome=outcome,
            effects=effects,
            target=self.target,
            started_at=self.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            evidence_ref=self.evidence_ref,
            checks=checks,
            outputs=outputs,
            error=error,
            log_truncated=log_truncated,
        )

    def summarized(self, *, omit_outputs: bool, omit_workspace: bool = False) -> Run:
        target = self.target
        if omit_workspace:
            target = Target(
                environment=target.environment,
                workspace="[see result artifact]",
                source_revision=target.source_revision,
                dirty=target.dirty,
            )
        return Run(
            run_id=self.run_id,
            workflow_ref=self.workflow_ref,
            profile=self.profile,
            purpose=self.purpose,
            state=self.state,
            outcome=self.outcome,
            effects=self.effects,
            target=target,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_ms=self.duration_ms,
            evidence_ref=self.evidence_ref,
            checks=self.checks,
            outputs=None if omit_outputs else self.outputs,
            error=self.error,
            log_truncated=self.log_truncated,
        )


class RunRequest(Contract):
    ref: str
    workspace: Path
    inputs: JsonObject = Field(default_factory=dict)
    profile: str = "default"
    environment: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    wait_seconds: float = Field(default=20.0, ge=0, le=20)


class Job(Contract):
    workflow: Workflow
    run: Run
    profile: str
    inputs: JsonObject
    validation: bool = False


class SearchRequest(Contract):
    query: str = Field(min_length=1, max_length=512)
    environment: str | None = None
    repository: str | None = None
    profile: str = "default"
    limit: int = Field(default=3, ge=1, le=3)


class Candidate(Contract):
    ref: str
    description: str
    environments: tuple[str, ...]
    effects: tuple[str, ...]
    required_inputs: tuple[str, ...]
    trust: Literal["trusted", "untrusted"]
    compatibility: Literal["runtime_check_required"] = "runtime_check_required"
    details_required: bool = False


class SearchResult(Contract):
    results: tuple[Candidate, ...]
    more: bool


class InspectRequest(Contract):
    run_id: str
    view: View = "status"
    cursor: int = Field(default=0, ge=0, le=1073741824)
    max_bytes: int = Field(default=4096, ge=256, le=16384)
    wait_seconds: float = Field(default=0.0, ge=0, le=20)


class Page(Contract):
    run_id: str
    view: View
    text: str
    next_cursor: int | None


class RunSummary(Contract):
    run: Run
    outputs_omitted: bool = False
    finalization_pending: bool = False


class FinalizationReport(Contract):
    run_id: str
    pending: bool
    active: bool = False
    errors: tuple[Failure, ...] = ()


class RecoveryReport(Contract):
    runs: tuple[FinalizationReport, ...]


class FailureReport(Contract):
    run_id: str
    state: State
    outcome: Outcome
    error: Failure | None
    checks: tuple[Check, ...]
    more: bool = False


class Cancellation(Contract):
    run_id: str
    cancellation_requested: bool
    effects: Effects
