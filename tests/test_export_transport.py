import http.client
from pathlib import Path

import pytest

from owlmatic.automatic_export import ExportWatcher
from owlmatic.bootstrap import create_application
from owlmatic.domain import Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.http_export import HttpSnapshotSender
from owlmatic.observability_config import ExportAuth, ExportConfiguration, HttpDestination
from tests.fakes import FakeClock
from tests.test_export import config, exporter


def test_http_adapter_binds_secret_to_header_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    class Connection:
        status = 302
        closed = False

        def __init__(self, host: str, port: int | None, *, timeout: int) -> None:
            assert host == "127.0.0.1" and timeout == 5

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            assert method == "POST"
            calls.append((path, body, headers))

        def getresponse(self) -> "Connection":
            return self

        def close(self) -> None:
            Connection.closed = True

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    monkeypatch.setenv("TEST_EXPORT_TOKEN", "private-test-token")
    configuration = ExportConfiguration(
        mode="manual",
        destination=HttpDestination(
            endpoint="http://127.0.0.1:8765/api/v1/snapshots", auth=ExportAuth(token_env="TEST_EXPORT_TOKEN")
        ),
    )
    with pytest.raises(OwlError) as caught:
        HttpSnapshotSender().send(configuration, "a" * 32, b"{}")
    assert caught.value.code == "EXPORT_REJECTED" and "private-test-token" not in str(caught.value)
    assert len(calls) == 1 and calls[0][2]["Authorization"] == "Bearer private-test-token"
    assert Connection.closed
    Connection.status = 503
    with pytest.raises(OwlError) as caught:
        HttpSnapshotSender().send(configuration, "a" * 32, b"{}")
    assert caught.value.code == "EXPORT_TRANSIENT"


def test_watcher_retries_transient_delivery_with_injected_clock(tmp_path: Path, workflow: Workflow) -> None:
    exports, sender = exporter(tmp_path, workflow)
    exports.configure(config(tmp_path / "source.yaml", mode="after_workflow"))
    sender.failure = OwlError("EXPORT_TRANSIENT", "temporary")
    app = create_application(tmp_path)
    assert isinstance(exports.clock, FakeClock)
    ExportWatcher(app.execution.runs, exports, exports.clock, app.dashboard).watch("run_" + f"{1:032x}", 1000)
    assert len(sender.calls) == 5
    assert len(set(sender.calls)) == 1
    assert exports.status().attempts == 5
