"""Export-safe v2 measurements: aggregates always, workflow identities only by consent."""

from typing import Literal

from pydantic import Field

from .domain import Contract
from .measurement.contracts import Assessment


class MeasurementMetrics(Contract):
    scope: Literal["retained_measurement_history"] = "retained_measurement_history"
    observed_tasks: int = Field(ge=0)
    measured_tasks: int = Field(ge=0)
    unattributed_tasks: int = Field(ge=0)
    measured_agent_tokens: int | None = Field(default=None, ge=0)
    estimated_operational_savings: int | None = None
    estimated_net_savings: int | None = None
    estimated_context_reference_tokens: int | None = None
    context_bytes_reduced: int | None = None
    latest_observation_at: str | None = None
    workflows: tuple[Assessment, ...] | None = None
    method: Literal["observed-history-v1"] = "observed-history-v1"
    note: str = "Historical comparison, not a guaranteed minimum; reference tokens are not provider billing"
