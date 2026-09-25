"""Optional delivery. Network failures never enter the workflow execution path."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import OwlError
from .export_contracts import StatisticsSnapshot
from .export_ports import ExportStore, SnapshotSender, StatisticsSource
from .export_projection import snapshot
from .export_state import DeliveryReport, ExportState, ExportStatus, PendingExport
from .failures import public_failure
from .observability_config import ExportConfiguration
from .ports import Clock
from .serialization import encode
from .statistics import StatisticsRequest


def payload_digest(value: StatisticsSnapshot) -> str:
    return hashlib.sha256(
        value.model_dump_json(exclude={"snapshot_id", "source_id", "sequence", "generated_at"}).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ExportService:
    statistics: StatisticsSource
    store: ExportStore
    sender: SnapshotSender
    clock: Clock
    new_id: Callable[[], str]

    def configure(self, path: Path) -> ExportStatus:
        with self.store.locked():
            self.store.install(path)
            self._synchronize()
        return self.status()

    def disable(self) -> ExportStatus:
        with self.store.locked():
            self.store.disable()
            self._synchronize()
        return self.status()

    def _synchronize(self) -> tuple[ExportConfiguration, ExportState]:
        config = self.store.configuration().export
        digest = hashlib.sha256(encode(config).encode()).hexdigest()
        state = self.store.load()
        if state.configuration_digest != digest:
            state = ExportState(
                source_id=state.source_id, sequence=state.sequence, configuration_digest=digest
            )
            self.store.save(state)
        if (
            state.pending
            and self.clock.epoch() - state.pending.created_at
            >= config.delivery.pending_retention_days * 86400
        ):
            state = ExportState(
                source_id=state.source_id,
                sequence=state.sequence,
                configuration_digest=digest,
                last_payload_digest=state.last_payload_digest,
            )
            self.store.save(state)
        return config, state

    def status(self) -> ExportStatus:
        with self.store.locked():
            _, state = self._synchronize()
            pending = state.pending
            return ExportStatus(
                configuration=self.store.configuration(),
                pending_snapshot_id=pending.snapshot.snapshot_id if pending else None,
                attempts=pending.attempts if pending else 0,
                last_failure=pending.last_failure if pending else None,
            )

    def _prepare(
        self, config: ExportConfiguration, state: ExportState, automatic: bool = False
    ) -> ExportState:
        if config.mode == "disabled":
            raise OwlError("EXPORT_DISABLED", "Configure observability.yaml before exporting")
        if state.pending:
            return state
        report = self.statistics.report(StatisticsRequest(days=config.window_days))
        value = snapshot(
            report, config.include, state.source_id or self.new_id(), self.new_id(), state.sequence + 1
        )
        if automatic and payload_digest(value) == state.last_payload_digest:
            return state
        if len(encode(value).encode()) > config.delivery.max_pending_bytes:
            raise OwlError("EXPORT_TOO_LARGE", "Reduce the reporting window or shared breakdowns")
        updated = ExportState(
            source_id=value.source_id,
            sequence=value.sequence,
            configuration_digest=state.configuration_digest,
            last_payload_digest=state.last_payload_digest,
            pending=PendingExport(snapshot=value, created_at=self.clock.epoch()),
        )
        self.store.save(updated)
        return updated

    def preview(self) -> StatisticsSnapshot:
        with self.store.locked():
            config, state = self._synchronize()
            prepared = self._prepare(config, state)
            assert prepared.pending
            return prepared.pending.snapshot

    def push(self, *, retry: bool = False, automatic: bool = False) -> DeliveryReport:
        with self.store.locked():
            config, state = self._synchronize()
            if config.mode == "disabled" or (automatic and config.mode != "after_workflow"):
                return DeliveryReport(status="disabled")
            if retry and state.pending is None:
                return DeliveryReport(status="empty")
            state = self._prepare(config, state, automatic)
            pending = state.pending
            if pending is None:
                return DeliveryReport(status="unchanged")
            if not retry:
                if pending.attempts >= config.delivery.max_attempts or (
                    pending.last_failure and pending.last_failure.code != "EXPORT_TRANSIENT"
                ):
                    return DeliveryReport(
                        status="failed",
                        snapshot_id=pending.snapshot.snapshot_id,
                        failure=pending.last_failure,
                    )
                delay = pending.next_attempt_at - self.clock.epoch()
                if delay > 0:
                    return DeliveryReport(status="deferred", retry_after_seconds=delay)
            body = encode(pending.snapshot).encode()
            try:
                self.sender.send(config, pending.snapshot.snapshot_id, body)
            except Exception as error:
                failure = public_failure(error)
                delay = min(
                    config.delivery.retry_max_seconds,
                    config.delivery.retry_initial_seconds * 2 ** min(pending.attempts, 20),
                )
                failed = PendingExport(
                    snapshot=pending.snapshot,
                    created_at=pending.created_at,
                    attempts=pending.attempts + 1,
                    next_attempt_at=self.clock.epoch() + delay,
                    last_failure=failure,
                )
                self.store.save(
                    ExportState(
                        source_id=state.source_id,
                        sequence=state.sequence,
                        configuration_digest=state.configuration_digest,
                        last_payload_digest=state.last_payload_digest,
                        pending=failed,
                    )
                )
                return DeliveryReport(
                    status="failed",
                    snapshot_id=pending.snapshot.snapshot_id,
                    failure=failure,
                    retry_after_seconds=delay
                    if failure.code == "EXPORT_TRANSIENT" and failed.attempts < config.delivery.max_attempts
                    else 0,
                )
            self.store.save(
                ExportState(
                    source_id=state.source_id,
                    sequence=state.sequence,
                    configuration_digest=state.configuration_digest,
                    last_payload_digest=payload_digest(pending.snapshot),
                )
            )
            return DeliveryReport(
                status="delivered", snapshot_id=pending.snapshot.snapshot_id, payload_bytes=len(body)
            )
