"""Explicit contracts for one controlled reproduction experiment."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)


Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{1,80}$")]


class Environment(Contract):
    worker_digest: Digest
    runtime: Identifier
    configuration: Identifier


class LineItem(Contract):
    item_id: Identifier
    amount_cents: int = Field(gt=0, le=1000000)


class JobInput(Contract):
    account: Identifier
    items: tuple[LineItem, ...] = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0, le=1000000)

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate invoice items")
        return self


class AccountState(Contract):
    revision: int = Field(ge=0)
    balance_cents: int = Field(ge=0)


class Signature(Contract):
    stage: Identifier
    exception: Identifier
    code: Identifier


class Incident(Contract):
    task: Literal["invoice.reconcile"] = "invoice.reconcile"
    original_execution: Identifier
    inputs: JobInput
    initial_state: AccountState
    environment: Environment
    failure: Signature
    replay_effects: Literal["local_only", "external"]


class Inputs(Contract):
    database: str = Field(min_length=1, max_length=1024)
    execution_id: Identifier
    max_attempts: int = Field(default=3, ge=1, le=10)
    attempt_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    total_timeout_seconds: float = Field(default=15.0, gt=0, le=60)


class Observation(Contract):
    attempt: int = Field(ge=1, le=10)
    inputs_sha256: Digest
    environment: Environment
    initial_state: AccountState
    final_state: AccountState
    state: Literal["succeeded", "failed"]
    signature: Signature | None = None

    @model_validator(mode="after")
    def failure_evidence(self) -> Self:
        if (self.state == "failed") != (self.signature is not None):
            raise ValueError("Failed attempts require a signature; successful attempts cannot have one")
        return self


class Attempt(Contract):
    number: int
    finding: Literal["clean", "target_failure", "different_failure", "incomparable", "unknown"]
    observation: Observation | None = None


class Result(Contract):
    finding: Literal["reproduced", "not_reproduced", "inconclusive", "blocked"]
    reason: str
    attempts: tuple[Attempt, ...] = ()
    diagnosis_required: Literal[True] = True
    proves_fixed: Literal[False] = False


class Summary(Contract):
    finding: Literal["reproduced", "not_reproduced", "inconclusive", "blocked"]
    reason: str
    attempts: int
    clean_attempts: int
    matching_failures: int
    diagnosis_required: Literal[True] = True
    proves_fixed: Literal[False] = False
    evidence_file: str = "attempts.json"


class ReplayUnavailable(Exception):
    """No trustworthy execution result; replay must stop instead of retrying blindly."""


class SnapshotUnavailable(Exception):
    """A complete, supported historical snapshot could not be extracted."""
