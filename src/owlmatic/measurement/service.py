"""Measurement use cases. Host decoding, SQLite, and transports stay outside this module."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..errors import OwlError
from ..ports import Clock, RunRepository, WorkflowRepository
from .contracts import (
    Assessment,
    BaselineLink,
    CostCoverage,
    Emission,
    ImportRequest,
    ImportResult,
    MeasurementConfiguration,
    MeasurementReport,
    WatchResult,
)
from .estimation import assess
from .ports import ExposureCounter, HistoryReader, MeasurementRepository


@dataclass(frozen=True)
class MeasurementService:
    repository: MeasurementRepository
    history: HistoryReader
    counter: ExposureCounter
    workflows: WorkflowRepository
    runs: RunRepository
    clock: Clock
    configuration: Callable[[], MeasurementConfiguration]
    changed: Callable[[], None]

    def check_capture_task(self, task_id: str) -> None:
        task = next((task for task in self.repository.tasks() if task.task_id == task_id), None)
        if task is None or task.purpose != "manual" or not task.complete or not task.verified:
            raise OwlError("BASELINE_INCOMPLETE", "Capture requires a complete verified manual task")

    def capture_draft(self, path: Path, task_id: str) -> None:
        self.check_capture_task(task_id)
        self.repository.capture_draft(path, task_id)

    def bind_draft(self, path: Path, ref: str) -> None:
        task_id = self.repository.draft_task(path)
        if task_id:
            self.baseline(BaselineLink(workflow_ref=ref, task_id=task_id))

    def ingest(self, request: ImportRequest) -> ImportResult:
        if not self.configuration().enabled:
            raise OwlError(
                "MEASUREMENT_DISABLED", "Enable measurement in observability YAML before importing history"
            )
        observation = self.history.read(request, self.clock.iso())
        if observation.run_ids:
            runs = tuple(self.runs.get(run_id) for run_id in observation.run_ids)
            refs = {run.workflow_ref for run in runs}
            if request.workflow_ref and refs != {request.workflow_ref}:
                raise OwlError("ATTRIBUTION_CONFLICT", "Run references do not match the selected workflow")
            ref = next(iter(refs)) if len(refs) == 1 else None
            verified = all(
                run.purpose == "workflow"
                and run.state == "completed"
                and run.outcome in {"pass", "fail"}
                and run.effects in {"none", "completed"}
                for run in runs
            )
            observation = observation.attributed(ref, verified, "workflow_evidence")
        elif request.purpose == "reuse" and request.verified:
            raise OwlError("VERIFICATION_REQUIRED", "Reuse credit requires linked Owlmatic run evidence")
        if observation.workflow_ref:
            self.workflows.get(observation.workflow_ref)
        changed = self.repository.put(observation)
        if changed:
            self.changed()
        return ImportResult(observation=observation, changed=changed)

    def baseline(self, link: BaselineLink) -> BaselineLink:
        self.workflows.get(link.workflow_ref)
        task = next((task for task in self.repository.tasks() if task.task_id == link.task_id), None)
        if (
            task is None
            or task.purpose != "manual"
            or not task.verified
            or not task.complete
            or not task.model
        ):
            raise OwlError(
                "BASELINE_INCOMPLETE", "Select a complete, verified manual task with a known model"
            )
        self.repository.link(link)
        self.changed()
        return link

    def declare(self, coverage: CostCoverage) -> CostCoverage:
        self.workflows.get(coverage.workflow_ref)
        self.repository.declare(coverage)
        self.changed()
        return coverage

    def assessment(self, ref: str) -> Assessment:
        self.workflows.get(ref)
        tasks = self.repository.tasks()
        linked = {link.task_id for link in self.repository.links(ref)}
        return assess(
            ref, tasks, tuple(task for task in tasks if task.task_id in linked), self.repository.coverage(ref)
        )

    def report(self) -> MeasurementReport:
        tasks = self.repository.tasks()
        refs = sorted({task.workflow_ref for task in tasks if task.workflow_ref})
        return MeasurementReport(
            assessments=tuple(self.assessment(ref) for ref in refs),
            unattributed_tasks=sum(task.workflow_ref is None and task.purpose != "manual" for task in tasks),
            latest_observation_at=max((task.observed_at for task in tasks), default=None),
        )

    def record_emission(self, operation: str, run_id: str | None, text: str) -> None:
        if self.configuration().enabled:
            self.repository.emit(
                Emission(
                    operation=operation,
                    run_id=run_id,
                    emitted_at=self.clock.iso(),
                    exposure=self.counter.count((text,)),
                )
            )

    def watch(self, once: bool = False) -> Iterator[WatchResult]:
        while True:
            config = self.configuration()
            if not config.enabled:
                raise OwlError("MEASUREMENT_DISABLED", "Measurement watcher is disabled")
            imported = 0
            failures: list[str] = []
            for request in config.sources:
                try:
                    imported += int(self.ingest(request).changed)
                except (OwlError, OSError, ValueError) as error:
                    failures.append(error.code if isinstance(error, OwlError) else "HISTORY_UNAVAILABLE")
            since = datetime.fromtimestamp(self.clock.epoch(), UTC) - timedelta(days=config.retention_days)
            self.repository.retain_since(since.isoformat())
            self.changed()  # also flushes a previously pending export or a retention change
            yield WatchResult(imported=imported, failures=tuple(failures))
            if once:
                return
            self.clock.sleep(config.poll_seconds)
