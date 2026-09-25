"""Real SQLite and host decoding, fake time and transport: no model or network calls."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from owlmatic.bootstrap import Application, create_application
from owlmatic.domain import Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.measurement.repository import SqliteMeasurements
from owlmatic.infrastructure.sqlite import SqliteDatabase
from owlmatic.measurement.contracts import BaselineLink, CostCoverage, ImportRequest, MeasurementConfiguration
from tests.fakes import FakeClock
from tests.test_measurement_estimation import task
from tests.test_statistics import NOW, invocation


def application(root: Path, workflow: Workflow) -> Application:
    app = create_application(root)
    app.catalog.workflows.add_draft(workflow)
    (root / "observability.yaml").write_text("version: 2\nmeasurement:\n  enabled: true\n")
    return app


def history(path: Path, tokens: int = 100, run_id: str | None = None, ref: str = "") -> Path:
    output = json.dumps({"run": {"run_id": run_id, "workflow_ref": ref}}) if run_id else "manual result"
    events = [
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "call", "type": "command_execution", "aggregated_output": output},
        },
        {"type": "turn.completed", "usage": {"input_tokens": tokens, "output_tokens": 10}},
    ]
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    return path


def test_incomplete_import_updates_once_and_notifies_after_late_usage(
    tmp_path: Path, workflow: Workflow
) -> None:
    app = application(tmp_path / "home", workflow)
    path = tmp_path / "history.jsonl"
    path.write_text('{"type":"turn.started"}\n')
    notifications: list[bool] = []
    service = replace(app.measurements, changed=lambda: notifications.append(True))
    request = ImportRequest(host="codex", session=path, task_id="late", model="test")
    assert not service.ingest(request).observation.complete
    assert not service.ingest(request).changed
    history(path)
    assert service.ingest(request).observation.complete
    assert not service.ingest(request).changed
    assert len(notifications) == 2 and len(service.repository.tasks()) == 1
    history(path, tokens=999)
    with pytest.raises(OwlError, match="immutable"):
        service.ingest(request)


def test_overlapping_and_copied_history_is_not_double_counted(tmp_path: Path, workflow: Workflow) -> None:
    service = application(tmp_path / "home", workflow).measurements
    path = history(tmp_path / "history.jsonl")
    service.ingest(ImportRequest(host="codex", session=path, task_id="one", model="test"))
    for request in (
        ImportRequest(host="codex", session=path, task_id="two", model="test"),
        ImportRequest(host="codex", session=path, task_id="range", model="test", first_line=2),
        ImportRequest(host="codex", session=history(tmp_path / "copy.jsonl"), task_id="copy", model="test"),
    ):
        with pytest.raises(OwlError) as failure:
            service.ingest(request)
        assert failure.value.code == "DUPLICATE_HISTORY"
    assert len(service.repository.tasks()) == 1


def test_run_correlation_baseline_and_creation_coverage(tmp_path: Path, workflow: Workflow) -> None:
    app = application(tmp_path / "home", workflow)
    run = invocation(1, workflow.ref, effects="none")
    app.execution.runs.create(run, None, "fixture", None)
    manual = history(tmp_path / "manual.jsonl", 1000)
    app.measurements.ingest(
        ImportRequest(
            host="codex",
            session=manual,
            task_id="manual",
            purpose="manual",
            model="test",
            workload="small",
            verified=True,
        )
    )
    app.measurements.baseline(BaselineLink(workflow_ref=workflow.ref, task_id="manual"))
    reuse = history(tmp_path / "reuse.jsonl", 100, run.run_id, workflow.ref)
    imported = app.measurements.ingest(
        ImportRequest(host="codex", session=reuse, task_id="reuse", model="test", workload="small")
    )
    assert imported.observation.workflow_ref == workflow.ref
    assert imported.observation.verification_basis == "workflow_evidence"
    result = app.measurements.assessment(workflow.ref)
    assert result.estimated_operational_savings == 900 and result.estimated_net_savings is None
    # Explicit zero-cost attestation is different from missing costs.
    app.measurements.declare(
        CostCoverage(workflow_ref=workflow.ref, setup_complete=True, maintenance_complete=True)
    )
    assert app.measurements.assessment(workflow.ref).estimated_net_savings == 900
    draft = app.authoring.capture("captured", tmp_path / "draft", source_task="manual")
    assert app.measurements.repository.draft_task(draft.draft) == "manual"
    assert app.authoring.validate(draft.draft, schema_only=True).valid
    assert not app.measurements.repository.links(app.authoring.validate(draft.draft, schema_only=True).ref)


def test_grouped_runs_are_unattributed_and_can_only_be_charged_once(
    tmp_path: Path, workflow: Workflow
) -> None:
    app = application(tmp_path / "home", workflow)
    second = workflow.model_copy(update={"ref": "other/workflow@" + "a" * 64})
    app.catalog.workflows.add_draft(second)
    runs = (invocation(1, workflow.ref, effects="none"), invocation(2, second.ref, effects="none"))
    for run in runs:
        app.execution.runs.create(run, None, "fixture", None)
    path = history(tmp_path / "group.jsonl")
    request = ImportRequest(
        host="codex", session=path, task_id="group", model="test", run_ids=tuple(run.run_id for run in runs)
    )
    result = app.measurements.ingest(request)
    assert result.observation.workflow_ref is None
    assert app.measurements.report().unattributed_tasks == 1
    other = history(tmp_path / "other.jsonl", tokens=101)
    with pytest.raises(OwlError) as error:
        app.measurements.ingest(
            ImportRequest(
                host="codex", session=other, task_id="duplicate", model="test", run_ids=(runs[0].run_id,)
            )
        )
    assert error.value.code == "RUN_ALREADY_ATTRIBUTED"
    assert len(app.measurements.repository.tasks()) == 1


def test_disabled_collection_does_not_read_history_or_record_output(tmp_path: Path) -> None:
    app = create_application(tmp_path)
    with pytest.raises(OwlError) as failure:
        app.measurements.ingest(ImportRequest(host="codex", session=tmp_path / "missing", task_id="no"))
    assert failure.value.code == "MEASUREMENT_DISABLED"
    app.record_response("run", "private output", None)
    with SqliteDatabase(tmp_path).connect() as db:
        assert db.execute("SELECT COUNT(*) FROM measurement_emissions").fetchone()[0] == 0


def test_emitted_output_is_not_saved_as_raw_text_or_credited_as_visible(
    tmp_path: Path, workflow: Workflow
) -> None:
    app = application(tmp_path / "home", workflow)
    app.record_response("run", "PRIVATE-MATERIAL", None)
    with SqliteDatabase(tmp_path / "home").connect() as db:
        raw = db.execute("SELECT data FROM measurement_emissions").fetchone()[0]
    assert "PRIVATE-MATERIAL" not in raw and "emitted_not_confirmed_visible" in raw
    assert app.measurements.report().assessments == ()


def test_retention_invalidates_coverage_and_removes_associations(tmp_path: Path) -> None:
    repository = SqliteMeasurements(SqliteDatabase(tmp_path))
    observation = task("create", 100, purpose="creation")
    assert observation.workflow_ref
    repository.put(observation)
    repository.declare(
        CostCoverage(workflow_ref=observation.workflow_ref, setup_complete=True, maintenance_complete=True)
    )
    repository.capture_draft(tmp_path / "draft", observation.task_id)
    repository.retain_since("2026-09-26T00:00:00+00:00")
    assert repository.tasks() == ()
    assert not repository.coverage(observation.workflow_ref).setup_complete
    assert repository.draft_task(tmp_path / "draft") is None


def test_watcher_only_imports_configured_files_and_reports_failures(
    tmp_path: Path, workflow: Workflow
) -> None:
    app = application(tmp_path / "home", workflow)
    path = history(tmp_path / "selected.jsonl")
    history(tmp_path / "unselected.jsonl", tokens=12345)
    config = MeasurementConfiguration(
        enabled=True,
        sources=(
            ImportRequest(host="codex", session=path, task_id="one", model="test"),
            ImportRequest(host="codex", session=tmp_path / "missing", task_id="missing"),
        ),
    )
    service = replace(app.measurements, configuration=lambda: config, clock=FakeClock(NOW))
    result = tuple(service.watch(once=True))[0]
    assert result.imported == 1 and result.failures == ("HISTORY_LIMIT",)
    assert len(service.repository.tasks()) == 1
