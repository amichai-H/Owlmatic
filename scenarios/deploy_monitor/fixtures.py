"""Mock-only fault plans and synthetic observations, separate from verification logic."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from .workflow.monitoring.contracts import (
    Contract,
    DeploymentStatus,
    DeployRequest,
    Event,
    LogPage,
    LogQuery,
    Receipt,
    Replica,
)

Fault = Literal[
    "healthy",
    "wrong_version",
    "missing_replica",
    "late_error",
    "boundary_error",
    "missing_logs",
    "stale_logs",
    "gap",
    "cursor_loop",
    "drift",
    "unavailable",
    "malformed",
]


class Plan(Contract):
    fault: Fault = "healthy"
    rollout_seconds: float = Field(default=1.0, ge=0, le=60)
    error_after_seconds: float = Field(default=30.0, ge=0, le=300)
    ingestion_delay_seconds: float = Field(default=0.0, ge=0, le=60)


class Record(Contract):
    receipt: Receipt
    accepted_at: float
    request: DeployRequest
    plan: Plan


def status(record: Record, now: float) -> DeploymentStatus:
    ready = now >= record.accepted_at + record.plan.rollout_seconds
    drift = record.plan.fault == "drift" and now >= record.accepted_at + record.plan.error_after_seconds
    replicas = tuple(
        Replica(
            name=name,
            version="old"
            if drift or (index == 0 and record.plan.fault == "wrong_version")
            else record.receipt.version,
            ready=ready,
        )
        for index, name in enumerate(record.receipt.replicas)
    )
    if record.plan.fault == "missing_replica":
        replicas = replicas[:-1]
    return DeploymentStatus(deployment_id=record.receipt.deployment_id, observed_at=now, replicas=replicas)


def log_page(record: Record, query: LogQuery, now: float) -> LogPage:
    plan = record.plan
    available = now - plan.ingestion_delay_seconds >= query.end
    missing = plan.fault in {"missing_logs", "stale_logs"} or not available
    # Each fully available query is a stable snapshot, including every page.
    events: list[Event] = []
    if not missing:
        first = max(0, math.ceil(query.start - record.accepted_at))
        last = math.floor(query.end - record.accepted_at)
        for tick in range(first, last + 1):
            for index, replica in enumerate(record.receipt.replicas):
                events.append(
                    Event(
                        sequence=tick * 4 + index,
                        at=record.accepted_at + tick,
                        replica=replica,
                        version=record.receipt.version,
                        level="heartbeat",
                    )
                )
        error_at = record.accepted_at + plan.rollout_seconds + plan.error_after_seconds
        if plan.fault in {"late_error", "boundary_error"} and query.start <= error_at <= query.end:
            events.append(
                Event(
                    sequence=math.floor(error_at - record.accepted_at) * 4 + 3,
                    at=error_at,
                    replica=record.receipt.replicas[0],
                    version=record.receipt.version,
                    level="error",
                )
            )
        events.sort(key=lambda event: event.sequence)
    chunk = events[query.cursor : query.cursor + 16]
    next_cursor = query.cursor + 16 if query.cursor + 16 < len(events) else None
    if plan.fault == "cursor_loop":
        next_cursor = query.cursor
    return LogPage(
        deployment_id=record.receipt.deployment_id,
        start=query.start,
        end=query.end,
        complete_through=query.start if missing else query.end,
        gap=plan.fault == "gap",
        events=tuple(chunk),
        next_cursor=next_cursor,
    )
