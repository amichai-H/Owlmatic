"""Versioned wire contracts. JSON is validated at the transport boundary."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{1,80}$")]
Timestamp = Annotated[float, Field(ge=0)]


class Inputs(Contract):
    deploy_url: str
    logs_url: str
    service: Identifier
    version: Identifier
    request_key: Identifier
    monitor_minutes: float = Field(default=1.0, gt=0, le=5)
    rollout_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ingestion_grace_seconds: float = Field(default=5.0, ge=0, le=30)
    poll_seconds: float = Field(default=1.0, gt=0, le=5)


class DeployRequest(Contract):
    service: Identifier
    version: Identifier
    request_key: Identifier


class Receipt(Contract):
    deployment_id: Identifier
    service: Identifier
    version: Identifier
    replicas: tuple[Identifier, ...] = Field(min_length=1, max_length=10)


class Replica(Contract):
    name: Identifier
    version: Identifier
    ready: bool


class DeploymentStatus(Contract):
    deployment_id: Identifier
    observed_at: Timestamp
    replicas: tuple[Replica, ...] = Field(max_length=10)


class Event(Contract):
    sequence: int = Field(ge=0)
    at: Timestamp
    replica: Identifier
    version: Identifier
    level: Literal["heartbeat", "error"]


class LogQuery(Contract):
    deployment_id: Identifier
    start: Timestamp
    end: Timestamp
    cursor: int = Field(default=0, ge=0, le=10000)


class LogPage(Contract):
    deployment_id: Identifier
    start: Timestamp
    end: Timestamp
    complete_through: Timestamp
    gap: bool
    events: tuple[Event, ...] = Field(max_length=64)
    next_cursor: int | None = Field(default=None, ge=0, le=10000)


class Result(Contract):
    outcome: Literal["pass", "fail", "inconclusive"]
    reason: str
    deployment_id: str | None
    observed_seconds: float
    events_checked: int
    effects: Literal["completed", "unknown"]


class Unavailable(Exception):
    """The transport or an upstream contract cannot provide reliable evidence."""
