"""Local delivery state; these records are never exported."""

from typing import Literal

from pydantic import Field

from .domain import Contract, Failure
from .export_contracts import Identifier, StatisticsSnapshot
from .observability_config import ObservabilityConfiguration


class PendingExport(Contract):
    snapshot: StatisticsSnapshot
    created_at: float
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: float = 0
    last_failure: Failure | None = None


class ExportState(Contract):
    source_id: Identifier | None = None
    sequence: int = Field(default=0, ge=0)
    configuration_digest: str | None = None
    pending: PendingExport | None = None
    last_payload_digest: str | None = None


class ExportStatus(Contract):
    configuration: ObservabilityConfiguration
    pending_snapshot_id: str | None = None
    attempts: int = 0
    last_failure: Failure | None = None


class DeliveryReport(Contract):
    status: Literal["disabled", "delivered", "deferred", "failed", "unchanged", "empty"]
    snapshot_id: str | None = None
    payload_bytes: int = 0
    failure: Failure | None = None
    retry_after_seconds: float = 0
