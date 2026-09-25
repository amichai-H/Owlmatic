"""Independent fixtures; one frozen bundle; explicit expectations for each case."""

import os
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

from owlmatic.bootstrap import create_application
from owlmatic.domain import Catalog, RunRequest, RunSummary, SearchRequest
from owlmatic.serialization import yaml_model

from .fixtures import PROJECT, add_job, create_database
from .suite_contracts import Case, CaseReport, Suite, SuiteReport
from .workflow.reproduction.contracts import Inputs, Summary


def load_suite(path: Path) -> Suite:
    if path.stat().st_size > 65536:
        raise ValueError("Scenario YAML exceeds 64 KiB")
    return yaml_model(path.read_bytes(), Suite)


def prepare_case(database: Path, case: Case) -> None:
    add_job(
        database,
        case.id,
        case.worker,
        amounts=case.amounts_cents,
        revision=case.revision,
        external=case.replay_effects == "external",
    )
    with closing(sqlite3.connect(database)) as db:
        if case.snapshot == "missing_state":
            db.execute("DELETE FROM account_snapshots WHERE execution_id=?", (case.id,))
        elif case.snapshot == "missing_item":
            db.execute("DELETE FROM input_items WHERE execution_id=? AND ordinal=0", (case.id,))
        elif case.snapshot == "unsupported_task":
            db.execute("UPDATE failed_jobs SET task='email.send' WHERE execution_id=?", (case.id,))
        db.commit()


def run_suite(suite: Suite, root: Path) -> SuiteReport:
    root = root.resolve()
    if not root.is_relative_to(PROJECT):
        raise ValueError("Local lab output must stay within this checkout")
    directory = root / uuid.uuid4().hex[:8]
    database = directory / "jobs.sqlite"
    create_database(database)
    for case in suite.cases:
        prepare_case(database, case)
    (directory / "suite.json").write_text(suite.model_dump_json(indent=2))
    app = create_application(root / "home")
    catalog_name = "reproduction-lab"
    if any(c.name == catalog_name for c in app.catalog.list().catalogs):
        app.catalog.sync(catalog_name)
    else:
        app.catalog.add(Catalog(name=catalog_name, source=str(PROJECT / "scenarios/task_replay")))
    candidate = app.catalog.find(SearchRequest(query="lab.task-reproduce")).results[0]
    app.catalog.trust(candidate.ref)
    reports: list[CaseReport] = []
    previous_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + previous_path
    try:
        for case in suite.cases:
            options = Inputs(
                database=str(database.relative_to(PROJECT)),
                execution_id=case.id,
                max_attempts=case.max_attempts,
            )
            result = app.execution.run(
                RunRequest(
                    ref=candidate.ref,
                    workspace=PROJECT,
                    inputs=options.model_dump(mode="json"),
                    environment="simulation",
                    request_id=uuid.uuid4().hex,
                )
            )
            while result.run.state == "running":
                result = RunSummary(run=app.execution.wait(result.run.run_id, 20))
            (directory / f"{case.id}.json").write_text(result.model_dump_json(indent=2))
            if result.run.state == "completed":
                summary = Summary.model_validate(result.run.outputs)
                matched = (
                    summary.finding == case.expected.finding
                    and summary.attempts == case.expected.attempts
                    and (case.expected.reason is None or summary.reason == case.expected.reason)
                )
                reports.append(
                    CaseReport(
                        id=case.id,
                        run_id=result.run.run_id,
                        finding=summary.finding,
                        attempts=summary.attempts,
                        oracle_matched=matched,
                    )
                )
            else:
                reports.append(
                    CaseReport(
                        id=case.id,
                        run_id=result.run.run_id,
                        finding="execution_error",
                        attempts=0,
                        oracle_matched=False,
                    )
                )
    finally:
        os.environ["PATH"] = previous_path
    report = SuiteReport(
        workflow_ref=candidate.ref, cases=tuple(reports), passed=all(case.oracle_matched for case in reports)
    )
    (directory / "report.json").write_text(report.model_dump_json(indent=2))
    return report
