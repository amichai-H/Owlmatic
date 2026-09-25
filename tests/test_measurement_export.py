"""Privacy, version compatibility, and missing-evidence behavior at the export boundary."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from owlmatic.domain import Workflow
from owlmatic.export_contracts import ExportScope, Snapshot, StatisticsSnapshotV2
from owlmatic.export_projection import snapshot
from owlmatic.measurement.contracts import CostCoverage, MeasurementReport
from owlmatic.measurement.estimation import assess
from owlmatic.observability_config import ObservabilityConfiguration
from owlmatic.statistics import StatisticsRequest
from owlmatic.statistics_service import StatisticsService
from tests.conftest import ROOT
from tests.test_export import exporter
from tests.test_measurement_estimation import REF, task


def test_v2_projection_excludes_private_evidence_and_suppresses_unattributed_savings(
    tmp_path: Path, workflow: Workflow
) -> None:
    exports, _ = exporter(tmp_path, workflow)
    row = assess(
        REF,
        (task("private-task", 100),),
        (task("private-baseline", 1000, purpose="manual"),),
        CostCoverage(workflow_ref=REF, setup_complete=True, maintenance_complete=True),
    )
    measurement = MeasurementReport(assessments=(row,), unattributed_tasks=1, latest_observation_at=None)
    assert isinstance(exports.statistics, StatisticsService)
    stats = replace(exports.statistics, measurements=lambda: measurement)
    value = snapshot(
        stats.report(StatisticsRequest()),
        ExportScope(),
        "a" * 32,
        "b" * 32,
        1,
        version="2",
        source_label="Team demo",
        include_measurements=True,
    )
    assert isinstance(value, StatisticsSnapshotV2) and value.measurements
    assert value.measurements.estimated_operational_savings is None
    assert value.measurements.estimated_net_savings is None
    assert value.measurements.workflows is None
    assert value.measurements.measured_agent_tokens == 100
    for private in (
        "private-task",
        "private-baseline",
        "source_digest",
        "source_key",
        REF,
        "workspace",
        "session",
    ):
        assert private not in value.model_dump_json()
    assert TypeAdapter(Snapshot).validate_json(value.model_dump_json()) == value
    schema = json.loads((ROOT / "contracts/statistics-snapshot-v2.schema.json").read_text())
    assert schema == StatisticsSnapshotV2.model_json_schema()

    unknown_costs = MeasurementReport(
        assessments=(
            row.model_copy(
                update={"estimated_net_savings": None, "creation_tokens": None, "maintenance_tokens": None}
            ),
        ),
        unattributed_tasks=0,
    )
    stats = replace(stats, measurements=lambda: unknown_costs)
    detailed = snapshot(
        stats.report(StatisticsRequest()),
        ExportScope(workflow_identifiers=True),
        "a" * 32,
        "b" * 32,
        2,
        version="2",
        include_measurements=True,
    )
    # Production encoding omits None; unknown nested fields must survive disk and HTTP round trips.
    from owlmatic.export_state import ExportState, PendingExport
    from owlmatic.serialization import encode

    state = ExportState(
        source_id="a" * 32, sequence=2, pending=PendingExport(snapshot=detailed, created_at=0)
    )
    assert ExportState.model_validate_json(encode(state)) == state


def test_v2_explicit_sharing_and_v1_configuration_validation(tmp_path: Path, workflow: Workflow) -> None:
    exports, _ = exporter(tmp_path, workflow)
    value = snapshot(
        exports.statistics.report(StatisticsRequest()), ExportScope(), "a" * 32, "b" * 32, 1, version="2"
    )
    assert isinstance(value, StatisticsSnapshotV2) and value.measurements is None
    with pytest.raises(ValueError, match="consent"):
        StatisticsSnapshotV2.model_validate({**value.model_dump(), "measurements_shared": True})
    with pytest.raises(ValueError, match="version 2"):
        ObservabilityConfiguration.model_validate({"version": 1, "measurement": {"enabled": True}})


def test_disabling_export_preserves_local_measurement_configuration(
    tmp_path: Path, workflow: Workflow
) -> None:
    exports, _ = exporter(tmp_path, workflow)
    path = tmp_path / "configuration.yaml"
    path.write_text(
        'version: 2\nmeasurement:\n  enabled: true\nexport:\n  schema_version: "2"\n  source_label: Local team\n'
    )
    exports.configure(path)
    exports.disable()
    configuration = exports.store.configuration()
    assert configuration.version == 2 and configuration.measurement.enabled


def test_late_usage_refreshes_automatic_snapshot_without_another_workflow(
    tmp_path: Path, workflow: Workflow
) -> None:
    from dataclasses import dataclass, field

    from owlmatic.bootstrap import create_application
    from owlmatic.measurement.contracts import ImportRequest
    from owlmatic.observability_config import ExportConfiguration
    from tests.test_measurement_service import history
    from tests.test_statistics import invocation

    @dataclass
    class Sender:
        payloads: list[StatisticsSnapshotV2] = field(default_factory=list)

        def send(self, configuration: ExportConfiguration, snapshot_id: str, body: bytes) -> None:
            value = StatisticsSnapshotV2.model_validate_json(body)
            assert value.snapshot_id == snapshot_id
            self.payloads.append(value)

    app = create_application(tmp_path / "home")
    app.catalog.workflows.add_draft(workflow)
    config = tmp_path / "config.yaml"
    config.write_text(
        'version: 2\nmeasurement:\n  enabled: true\nexport:\n  schema_version: "2"\n  mode: after_workflow\n  include_measurements: true\n  destination:\n    endpoint: http://127.0.0.1:8765/api/v2/snapshots\n'
    )
    app.exports.configure(config)
    sender = Sender()
    exports = replace(app.exports, sender=sender)

    def notify() -> None:
        exports.push(automatic=True)

    service = replace(app.measurements, changed=notify)
    run = invocation(1, workflow.ref, effects="none")
    app.execution.runs.create(run, None, "fixture", None)
    path = tmp_path / "history.jsonl"
    path.write_text('{"type":"turn.started"}\n')
    request = ImportRequest(host="codex", session=path, task_id="late", model="test", run_ids=(run.run_id,))
    service.ingest(request)
    assert len(sender.payloads) == 1
    assert sender.payloads[0].measurements and sender.payloads[0].measurements.measured_agent_tokens is None
    history(path, run_id=run.run_id, ref=workflow.ref)
    service.ingest(request)
    assert len(sender.payloads) == 2
    assert sender.payloads[1].sequence == sender.payloads[0].sequence + 1
    assert sender.payloads[1].usage == sender.payloads[0].usage
    assert sender.payloads[1].measurements and sender.payloads[1].measurements.measured_agent_tokens == 110
    service.ingest(request)
    assert len(sender.payloads) == 2
