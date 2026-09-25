"""Deterministic delivery tests: no external network or provider token assumptions."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from owlmatic.automatic_export import AutomaticExport
from owlmatic.domain import Failure, Workflow
from owlmatic.errors import OwlError
from owlmatic.export_contracts import ExportScope, StatisticsSnapshot
from owlmatic.export_projection import snapshot
from owlmatic.export_service import ExportService
from owlmatic.infrastructure.export_store import FileExportStore
from owlmatic.observability_config import ExportAuth, ExportConfiguration, HttpDestination
from owlmatic.statistics import SavingsBaseline, StatisticsRequest
from tests.fakes import FakeClock
from tests.test_statistics import NOW, invocation, service


@dataclass
class Sender:
    calls: list[bytes] = field(default_factory=list)
    failure: OwlError | None = None

    def send(self, configuration: ExportConfiguration, snapshot_id: str, body: bytes) -> None:
        self.calls.append(body)
        assert StatisticsSnapshot.model_validate_json(body).snapshot_id == snapshot_id
        if self.failure:
            raise self.failure


def config(path: Path, mode: str = "manual", include: bool = False) -> Path:
    path.write_text(
        f"version: 1\nexport:\n  mode: {mode}\n  destination:\n    endpoint: http://127.0.0.1:8765/api/v1/snapshots\n  include:\n    workflow_identifiers: {str(include).lower()}\n"
    )
    return path


def exporter(tmp_path: Path, workflow: Workflow) -> tuple[ExportService, Sender]:
    stats, runs = service(tmp_path, workflow)
    runs.create(invocation(1, workflow.ref, outputs={"secret": "PRIVATE_OUTPUT"}), None, "fixture", None)
    stats.baseline(
        SavingsBaseline(ref=workflow.ref, manual_tokens=1000, owlmatic_tokens=100, source="PRIVATE_BASELINE")
    )
    sender = Sender()
    counter = iter(f"{i:032x}" for i in range(1, 100))
    exports = ExportService(stats, FileExportStore(tmp_path), sender, FakeClock(NOW), lambda: next(counter))
    return exports, sender


def test_disabled_means_no_delivery_and_preview_is_frozen(tmp_path: Path, workflow: Workflow) -> None:
    exports, sender = exporter(tmp_path, workflow)
    assert exports.push().status == "disabled" and sender.calls == []
    with pytest.raises(OwlError, match="Configure"):
        exports.preview()
    exports.configure(config(tmp_path / "source.yaml"))
    preview = exports.preview()
    assert exports.preview() == preview and sender.calls == []
    assert exports.push().snapshot_id == preview.snapshot_id
    assert len(sender.calls) == 1
    text = sender.calls[0].decode()
    for private in (
        workflow.ref,
        "PRIVATE_OUTPUT",
        "PRIVATE_BASELINE",
        "workspace",
        '"savings":',
        "baseline",
    ):
        assert private not in text


def test_retry_preserves_payload_and_restart_state(tmp_path: Path, workflow: Workflow) -> None:
    exports, sender = exporter(tmp_path, workflow)
    exports.configure(config(tmp_path / "source.yaml"))
    sender.failure = OwlError("EXPORT_TRANSIENT", "temporary")
    failed = exports.push()
    assert failed.status == "failed" and exports.status().attempts == 1
    assert exports.push().status == "deferred" and len(sender.calls) == 1
    restarted = ExportService(
        exports.statistics, FileExportStore(tmp_path), sender, exports.clock, exports.new_id
    )
    sender.failure = None
    assert restarted.push(retry=True).snapshot_id == failed.snapshot_id
    assert sender.calls[0] == sender.calls[1]
    assert restarted.status().pending_snapshot_id is None


def test_policy_change_discards_old_payload_and_disable_clears_queue(
    tmp_path: Path, workflow: Workflow
) -> None:
    exports, sender = exporter(tmp_path, workflow)
    exports.configure(config(tmp_path / "source.yaml", include=True))
    old = exports.preview()
    assert old.workflows
    exports.configure(config(tmp_path / "source.yaml"))
    new = exports.preview()
    assert new.snapshot_id != old.snapshot_id and new.workflows is None
    assert new.sequence > old.sequence and new.source_id == old.source_id
    exports.disable()
    assert exports.status().pending_snapshot_id is None and exports.push().status == "disabled"
    assert sender.calls == []


def test_automatic_coalesces_unchanged_snapshots_and_manual_mode_suppresses_it(
    tmp_path: Path, workflow: Workflow
) -> None:
    exports, sender = exporter(tmp_path, workflow)
    exports.configure(config(tmp_path / "source.yaml"))
    assert exports.push(automatic=True).status == "disabled"
    exports.configure(config(tmp_path / "source.yaml", mode="after_workflow"))
    assert exports.push(automatic=True).status == "delivered"
    assert exports.push(automatic=True).status == "unchanged"
    assert len(sender.calls) == 1


def test_scope_is_an_allowlist_even_when_all_options_enabled(tmp_path: Path, workflow: Workflow) -> None:
    exports, _ = exporter(tmp_path, workflow)
    report = exports.statistics.report(StatisticsRequest())
    value = snapshot(
        report,
        ExportScope(savings_estimates=True, daily_breakdown=True, workflow_identifiers=True),
        "a" * 32,
        "b" * 32,
        1,
    )
    assert value.savings and value.savings.net_tokens_saved == 900
    assert value.workflows and value.workflows[0].ref == workflow.ref
    assert "PRIVATE_BASELINE" not in value.model_dump_json()
    with pytest.raises(ValueError, match="scope"):
        StatisticsSnapshot.model_validate({**value.model_dump(), "shared": ExportScope()})


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/?token=secret",
        "file:///tmp/a",
        "https://example.com/#fragment",
        "https://example.com/\nsecret",
    ],
)
def test_endpoints_reject_unsafe_forms(endpoint: str) -> None:
    with pytest.raises(ValueError):
        HttpDestination(endpoint=endpoint)


def test_configuration_errors_do_not_trigger_automatic_work(tmp_path: Path) -> None:
    class Launcher:
        called = False

        def start(self, run_id: str, timeout: int, token_env: str | None) -> None:
            self.called = True
            raise AssertionError("Must not launch")

    store = FileExportStore(tmp_path)
    (tmp_path / "observability.yaml").write_text("version: 1\nexport:\n  unexpected: true\n")
    launcher = Launcher()
    AutomaticExport(store, launcher).observe("run_" + "1" * 32, 10)
    assert not launcher.called
    assert (
        Failure.model_validate_json((tmp_path / "export-diagnostic.json").read_bytes()).code
        == "EXPORT_CONFIG"
    )
    with pytest.raises(ValueError):
        ExportAuth(token_env="PYTHONPATH")


def test_pending_expiration_and_auth_errors_require_explicit_retry(
    tmp_path: Path, workflow: Workflow
) -> None:
    exports, sender = exporter(tmp_path, workflow)
    exports.configure(config(tmp_path / "source.yaml"))
    sender.failure = OwlError("EXPORT_AUTH", "missing binding")
    old = exports.push()
    assert exports.push().status == "failed" and len(sender.calls) == 1
    assert isinstance(exports.clock, FakeClock)
    exports.clock.sleep(8 * 86400)
    sender.failure = None
    assert exports.push().snapshot_id != old.snapshot_id


def test_schema_matches_published_contract(tmp_path: Path, workflow: Workflow) -> None:
    import json

    from jsonschema import Draft202012Validator

    from tests.conftest import ROOT

    exports, _ = exporter(tmp_path, workflow)
    exports.configure(config(tmp_path / "source.yaml"))
    value = exports.preview()
    schema = json.loads((ROOT / "contracts/statistics-snapshot-v1.schema.json").read_text())
    assert schema == StatisticsSnapshot.model_json_schema()
    Draft202012Validator(schema).validate(json.loads(value.model_dump_json(exclude_none=True)))
