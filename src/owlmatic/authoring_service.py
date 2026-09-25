from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .domain import RunRequest
from .errors import OwlError
from .execution_service import ExecutionService
from .messages import DraftReport, FixtureReport, ValidationReport
from .ports import BundleStore, WorkflowRepository


class DraftStore(Protocol):
    def create(self, name: str, output: Path | None) -> DraftReport: ...
    def workspace(self) -> AbstractContextManager[Path]: ...


@dataclass(frozen=True)
class AuthoringService:
    bundles: BundleStore
    workflows: WorkflowRepository
    execution: ExecutionService
    drafts: DraftStore

    def capture(self, name: str, output: Path | None = None) -> DraftReport:
        return self.drafts.create(name, output)

    def validate(
        self,
        directory: Path,
        profile: str = "default",
        environment: str | None = None,
        schema_only: bool = False,
    ) -> ValidationReport:
        workflow = self.bundles.snapshot(self.bundles.load(directory))
        if schema_only:
            return ValidationReport(ref=workflow.ref, valid=True)
        tests = workflow.manifest.tests
        if not any(t.outcome == "pass" for t in tests) or not any(t.outcome == "fail" for t in tests):
            raise OwlError("MISSING_TESTS", "Sharing validation requires both passing and failing fixtures")
        self.workflows.add_draft(workflow)
        reports: list[FixtureReport] = []
        for case in tests:
            with self.drafts.workspace() as workspace:
                run = self.execution.run(
                    RunRequest(
                        ref=workflow.ref,
                        inputs=case.inputs,
                        profile=profile,
                        environment=environment,
                        workspace=workspace,
                    ),
                    validation=True,
                ).run
                while run.state == "running":
                    run = self.execution.wait(run.run_id, 20)
                reports.append(
                    FixtureReport(
                        run_id=run.run_id,
                        expected=case.outcome,
                        actual=run.outcome,
                        state=run.state,
                        matched=run.state == "completed" and run.outcome == case.outcome,
                    )
                )
        return ValidationReport(ref=workflow.ref, valid=all(r.matched for r in reports), tests=tuple(reports))
