"""Savings accounting uses fixed time and explicit examples, not live agent calls."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from owlmatic.bootstrap import create_application
from owlmatic.dashboard_service import DashboardService
from owlmatic.domain import CheckCounts, Failure, Run, Target, Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.dashboard import FileDashboard
from owlmatic.infrastructure.export_store import FileExportStore
from owlmatic.infrastructure.run_repository import SqliteRuns
from owlmatic.infrastructure.sqlite import SqliteDatabase
from owlmatic.infrastructure.statistics import SqliteStatistics
from owlmatic.presentation.dashboard import render_dashboard
from owlmatic.statistics import SavingsBaseline, StatisticsRequest
from owlmatic.statistics_service import StatisticsService
from tests.fakes import FakeClock

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp()


def invocation(index: int, ref: str, **overrides: object) -> Run:
    # Dynamic overrides are confined to test construction and immediately validated.
    return Run.model_validate(
        {
            "run_id": f"run_{index:032x}",
            "workflow_ref": ref,
            "purpose": "workflow",
            "state": "completed",
            "outcome": "pass",
            "target": Target(environment="test", workspace="/private-do-not-export"),
            "started_at": "2026-09-25T10:00:00+00:00",
            "finished_at": "2026-09-25T10:00:01+00:00",
            "duration_ms": 1000,
            "evidence_ref": f"run_{index:032x}",
            "checks": CheckCounts(required=1, passed=1, failed=0, skipped=0),
            **overrides,
        }
    )


def service(tmp_path: Path, workflow: Workflow) -> tuple[StatisticsService, SqliteRuns]:
    app = create_application(tmp_path)
    app.catalog.workflows.add_draft(workflow)
    return replace(app.statistics, clock=FakeClock(NOW)), SqliteRuns(SqliteDatabase(tmp_path), FakeClock(NOW))


def test_savings_charge_failed_attempts_and_setup_once(tmp_path: Path, workflow: Workflow) -> None:
    stats, runs = service(tmp_path, workflow)
    stats.baseline(
        SavingsBaseline(
            ref=workflow.ref,
            manual_tokens=1000,
            owlmatic_tokens=100,
            setup_tokens=250,
            source="test estimate",
        )
    )
    for run in (
        invocation(1, workflow.ref),
        invocation(
            2, workflow.ref, outcome="fail", checks=CheckCounts(required=1, passed=0, failed=1, skipped=0)
        ),
        invocation(
            3,
            workflow.ref,
            state="error",
            outcome="inconclusive",
            checks=None,
            error=Failure(code="TIMEOUT", message="test"),
        ),
        invocation(
            4,
            workflow.ref,
            state="running",
            outcome="inconclusive",
            finished_at=None,
            checks=None,
            duration_ms=0,
        ),
        invocation(5, workflow.ref, purpose="validation"),
        invocation(6, workflow.ref, purpose="unknown"),
        invocation(7, "another@version"),
    ):
        runs.create(run, None, "fixture", None)
    report = stats.report(StatisticsRequest(days=2))
    assert report.runs == 5 and report.terminal == 4 and report.verified == 3
    assert report.running == 1 and report.execution_errors == 1
    assert report.excluded_validation_runs == 1 and report.excluded_legacy_runs == 1
    assert report.modeled_runs == 3
    assert report.estimated_manual_tokens == 2000
    assert report.estimated_owlmatic_tokens == 300
    assert report.estimated_setup_tokens == 250
    assert report.estimated_net_tokens_saved == 1450
    assert report.daily[0].runs == 0
    assert report.daily[1].estimated_tokens_saved_before_setup == 1700
    assert report.duration_ms == 4000
    assert report.provider_tokens is None


def test_negative_savings_not_clamped_and_baseline_updates_are_explicit(
    tmp_path: Path, workflow: Workflow
) -> None:
    stats, runs = service(tmp_path, workflow)
    runs.create(invocation(1, workflow.ref), None, "fixture", None)
    stats.baseline(
        SavingsBaseline(
            ref=workflow.ref, manual_tokens=100, owlmatic_tokens=200, setup_tokens=500, source="estimate"
        )
    )
    assert stats.report(StatisticsRequest()).estimated_net_tokens_saved == -600
    stats.baseline(
        SavingsBaseline(ref=workflow.ref, manual_tokens=1000, owlmatic_tokens=200, source="revised")
    )
    assert stats.report(StatisticsRequest()).estimated_net_tokens_saved == 800
    assert len(stats.repository.baselines()) == 1


def test_missing_baseline_and_empty_history_are_unknown(tmp_path: Path, workflow: Workflow) -> None:
    stats, runs = service(tmp_path, workflow)
    empty = stats.report(StatisticsRequest())
    assert empty.estimated_net_tokens_saved is None and empty.runs == 0
    runs.create(invocation(1, workflow.ref), None, "fixture", None)
    report = stats.report(StatisticsRequest())
    assert report.estimated_net_tokens_saved is None and report.modeled_runs == 0
    assert "Baseline needed" in render_dashboard(report)


def test_window_uses_utc_and_duplicate_request_is_counted_once(tmp_path: Path, workflow: Workflow) -> None:
    stats, runs = service(tmp_path, workflow)
    # Local Sep 24, but Sep 25 UTC; included. Start boundary is inclusive.
    run = invocation(1, workflow.ref, started_at="2026-09-24T20:00:00-04:00")
    first = runs.create(run, "same-request", "fixture", None)
    second = runs.create(invocation(2, workflow.ref), "same-request", "fixture", None)
    assert first.run_id == second.run_id
    for run in (
        invocation(3, workflow.ref, started_at="2026-09-24T23:59:59+00:00"),
        invocation(4, workflow.ref, started_at="2026-09-25T12:00:01+00:00"),
    ):
        runs.create(run, None, "fixture", None)
    assert stats.report(StatisticsRequest(days=1)).runs == 1


def test_unverified_results_earn_no_manual_credit(tmp_path: Path, workflow: Workflow) -> None:
    stats, runs = service(tmp_path, workflow)
    runs.create(invocation(1, workflow.ref, outcome="inconclusive", effects="unknown"), None, "x", None)
    stats.baseline(
        SavingsBaseline(ref=workflow.ref, manual_tokens=1000, owlmatic_tokens=100, source="estimate")
    )
    report = stats.report(StatisticsRequest())
    assert report.verified == 0 and report.estimated_net_tokens_saved == -100


def test_baseline_requires_registered_exact_version_and_benchmark_samples(
    tmp_path: Path, workflow: Workflow
) -> None:
    stats, _ = service(tmp_path, workflow)
    with pytest.raises(OwlError, match="exact workflow reference"):
        stats.baseline(
            SavingsBaseline(ref="unknown", manual_tokens=100, owlmatic_tokens=10, source="estimate")
        )
    with pytest.raises(ValueError, match="sample size"):
        SavingsBaseline(
            ref=workflow.ref, manual_tokens=100, owlmatic_tokens=10, source="fixture", basis="benchmark"
        )
    with pytest.raises(ValueError):
        StatisticsRequest(days=0)


def test_html_escapes_untrusted_fields_and_does_not_export_run_data(
    tmp_path: Path, workflow: Workflow
) -> None:
    stats, runs = service(tmp_path, workflow)
    runs.create(invocation(1, workflow.ref, outputs={"secret": "do-not-export"}), None, "x", None)
    stats.baseline(
        SavingsBaseline(
            ref=workflow.ref, manual_tokens=100, owlmatic_tokens=10, source="<script>alert('bad')</script>"
        )
    )
    dashboard = DashboardService(stats, FileDashboard(tmp_path), FileExportStore(tmp_path))
    result = dashboard.generate(StatisticsRequest(days=1))
    html = result.path.read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "do-not-export" not in html and "run_0000" not in html
    assert "Content-Security-Policy" in html
    assert "http://" not in html and "https://" not in html
    assert result.path.stat().st_mode & 0o777 == 0o600
    assert result.path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(OwlError, match=".html"):
        dashboard.generate(StatisticsRequest(), tmp_path / "source.py")
    linked = tmp_path / "symlink.html"
    linked.symlink_to(result.path)
    with pytest.raises(OwlError):
        FileDashboard(tmp_path).write(stats.report(StatisticsRequest()), linked)


def test_schema_two_upgrade_preserves_runs_and_adds_statistics(tmp_path: Path, workflow: Workflow) -> None:
    stats, runs = service(tmp_path, workflow)
    run = invocation(1, workflow.ref)
    runs.create(run, None, "fixture", None)
    with runs.db.connect() as db:
        db.execute("DROP TABLE savings_baselines")
        db.execute("PRAGMA user_version=2")
    upgraded = SqliteDatabase(tmp_path)
    assert SqliteRuns(upgraded, FakeClock(NOW)).get(run.run_id) == run
    assert SqliteStatistics(upgraded).baselines() == ()
    assert stats.report(StatisticsRequest()).runs == 1


def test_cli_stats_and_dashboard_are_json(tmp_path: Path) -> None:
    for arguments in (("stats", "--days", "7", "--json"), ("dashboard", "--days", "7", "--json")):
        result = subprocess.run(
            [sys.executable, "-m", "owlmatic", *arguments],
            env={**os.environ, "OWLMATIC_HOME": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["days"] == 7
    assert (tmp_path / "reports/dashboard.html").is_file()
