"""Deploy once, then require complete evidence for a monotonic observation window."""

from __future__ import annotations

from typing import Literal

from .contracts import DeployRequest, Event, Inputs, LogQuery, Receipt, Result, Unavailable
from .ports import Clock, Deployments, Logs


class Monitor:
    def __init__(self, deployments: Deployments, logs: Logs, clock: Clock) -> None:
        self.deployments = deployments
        self.logs = logs
        self.clock = clock

    def run(self, inputs: Inputs) -> Result:
        receipt: Receipt | None = None
        started = self.clock.monotonic()
        observed = 0.0
        checked = 0

        def result(outcome: Literal["pass", "fail", "inconclusive"], reason: str) -> Result:
            return Result(
                outcome=outcome,
                reason=reason,
                deployment_id=receipt.deployment_id if receipt else None,
                observed_seconds=observed,
                events_checked=checked,
                effects="completed" if receipt else "unknown",
            )

        try:
            receipt = self.deployments.deploy(
                DeployRequest(
                    service=inputs.service,
                    version=inputs.version,
                    request_key=inputs.request_key,
                )
            )
            if (receipt.service, receipt.version) != (inputs.service, inputs.version):
                raise Unavailable("deployment receipt identity mismatch")
            if len(set(receipt.replicas)) != len(receipt.replicas):
                raise Unavailable("duplicate expected replica")
            while True:
                status = self.deployments.status(receipt.deployment_id)
                self._identity(receipt, status.deployment_id)
                if self.clock.monotonic() - started > inputs.rollout_timeout_seconds:
                    return result("fail", "rollout_timeout_or_wrong_version")
                if self._ready(receipt, tuple((r.name, r.version, r.ready) for r in status.replicas)):
                    break
                if self.clock.monotonic() - started >= inputs.rollout_timeout_seconds:
                    return result("fail", "rollout_timeout_or_wrong_version")
                self.clock.sleep(
                    min(
                        inputs.poll_seconds,
                        inputs.rollout_timeout_seconds - (self.clock.monotonic() - started),
                    )
                )

            epoch = status.observed_at
            began = self.clock.monotonic()
            duration = inputs.monitor_minutes * 60
            previous_watermark = 0.0
            while True:
                elapsed = self.clock.monotonic() - began
                observed = min(elapsed, duration)
                status = self.deployments.status(receipt.deployment_id)
                self._identity(receipt, status.deployment_id)
                if abs(status.observed_at - epoch - elapsed) > 2:
                    raise Unavailable("source clock changed or status is stale")
                if not self._ready(receipt, tuple((r.name, r.version, r.ready) for r in status.replicas)):
                    return result("fail", "version_or_readiness_regressed")
                end = epoch + observed
                events, watermark = self._interval(receipt, epoch, end)
                checked = len(events)
                if watermark < previous_watermark:
                    raise Unavailable("completeness watermark regressed")
                previous_watermark = watermark
                if any(event.level == "error" for event in events):
                    return result("fail", "error_in_observation_window")
                if any(event.version != inputs.version for event in events):
                    return result("fail", "version_changed_in_log_history")
                complete = watermark >= end and self._heartbeats(receipt, events, epoch, end)
                if elapsed >= duration and complete:
                    return result("pass", "version_and_complete_error_free_window_verified")
                if elapsed >= duration + inputs.ingestion_grace_seconds:
                    return result("inconclusive", "log_coverage_not_proven")
                self.clock.sleep(
                    min(inputs.poll_seconds, duration + inputs.ingestion_grace_seconds - elapsed)
                )
        except Unavailable as exc:
            return result("inconclusive", str(exc))

    @staticmethod
    def _identity(receipt: Receipt, deployment_id: str) -> None:
        if deployment_id != receipt.deployment_id:
            raise Unavailable("response belongs to another deployment")

    @staticmethod
    def _ready(receipt: Receipt, replicas: tuple[tuple[str, str, bool], ...]) -> bool:
        return (
            len(replicas) == len(receipt.replicas)
            and {name for name, _, _ in replicas} == set(receipt.replicas)
            and all(version == receipt.version and ready for _, version, ready in replicas)
        )

    def _interval(self, receipt: Receipt, start: float, end: float) -> tuple[tuple[Event, ...], float]:
        cursor = 0
        events: list[Event] = []
        watermark: float | None = None
        # Bounded full-window scans simplify the lab's consistency contract.
        for _ in range(128):
            page = self.logs.read(
                LogQuery(deployment_id=receipt.deployment_id, start=start, end=end, cursor=cursor)
            )
            self._identity(receipt, page.deployment_id)
            if page.start != start or page.end != end or page.gap:
                raise Unavailable("log interval missing or truncated")
            if watermark is not None and page.complete_through != watermark:
                raise Unavailable("pagination snapshot changed")
            watermark = page.complete_through
            for event in page.events:
                if event.replica not in receipt.replicas or not start <= event.at <= end:
                    raise Unavailable("uncorrelated event")
                if events and event.sequence <= events[-1].sequence:
                    raise Unavailable("duplicate or unordered event sequence")
                events.append(event)
            if page.next_cursor is None:
                return tuple(events), watermark
            if page.next_cursor <= cursor:
                raise Unavailable("pagination cursor did not advance")
            cursor = page.next_cursor
        raise Unavailable("log page budget exceeded")

    @staticmethod
    def _heartbeats(receipt: Receipt, events: tuple[Event, ...], start: float, end: float) -> bool:
        for replica in receipt.replicas:
            ticks = sorted(e.at for e in events if e.replica == replica and e.level == "heartbeat")
            if not ticks:
                return False
            boundaries = [start, *ticks, end]
            if any(right - left > 2.5 for left, right in zip(boundaries, boundaries[1:], strict=False)):
                return False
        return True
