"""Independent scenario oracles, frozen time, real read-only SQL, and disposable workers."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest

from scenarios.task_replay.fixtures import PROJECT, WorkerPlan, add_job, create_database
from scenarios.task_replay.suite import load_suite, prepare_case
from scenarios.task_replay.suite_contracts import Suite
from scenarios.task_replay.worker import describe, execute
from scenarios.task_replay.workflow.reproduction.contracts import (
    Environment,
    Incident,
    Inputs,
    Observation,
    ReplayUnavailable,
    SnapshotUnavailable,
    Summary,
)
from scenarios.task_replay.workflow.reproduction.database import SqliteIncidents
from scenarios.task_replay.workflow.reproduction.experiment import Experiment
from scenarios.task_replay.workflow.reproduction.local import (
    FixtureRunner,
    LocalEvidence,
    SystemClock,
    workspace_file,
)
from scenarios.task_replay.workflow.reproduction.service import Reproducer


@dataclass
class Clock:
    now: float = 0.0

    def monotonic(self) -> float:
        return self.now


@dataclass
class Runner:
    database: Path
    execution_id: str
    scratch: Path
    calls: int = 0

    def describe(self, timeout: float) -> Environment:
        return describe(self.database, self.execution_id)

    def execute(self, incident: Incident, attempt: int, timeout: float) -> Observation:
        self.calls += 1
        directory = self.scratch / str(attempt)
        directory.mkdir()
        result = execute(self.database, incident, attempt, directory)
        if result is None:
            raise ReplayUnavailable("lost_ack")
        return result


SUITE_PATH = PROJECT / "scenarios/task_replay/scenarios.yaml"


@pytest.mark.parametrize("case", load_suite(SUITE_PATH).cases, ids=lambda case: case.id)
def test_declared_scenarios_match_independent_oracles(tmp_path: Path, case: object) -> None:
    from scenarios.task_replay.suite_contracts import Case

    assert isinstance(case, Case)
    database = tmp_path / "jobs.sqlite"
    create_database(database)
    prepare_case(database, case)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    artifacts = tmp_path / "evidence"
    artifacts.mkdir()
    runner = Runner(database, case.id, tmp_path)
    experiment = Experiment(SqliteIncidents(database), Reproducer(runner, Clock()), LocalEvidence(artifacts))
    result = experiment.run(
        Inputs(database="jobs.sqlite", execution_id=case.id, max_attempts=case.max_attempts)
    )
    assert result.finding == case.expected.finding
    assert result.attempts == case.expected.attempts == runner.calls
    assert result.proves_fixed is False and result.diagnosis_required
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert (artifacts / "attempts.json").is_file()


def test_new_yaml_data_needs_no_new_python_or_workflow(tmp_path: Path) -> None:
    path = tmp_path / "new-case.yaml"
    path.write_text("""version: 1
recipe: invoice-replay
cases:
  - id: previously-unseen-job
    amounts_cents: [19, 250, 7800, 42]
    revision: 121
    max_attempts: 4
    worker:
      attempts: [clean, clean, clean, target_failure]
    expected: {finding: reproduced, attempts: 4}
""")
    case = load_suite(path).cases[0]
    database = tmp_path / "jobs.sqlite"
    create_database(database)
    prepare_case(database, case)
    incident = SqliteIncidents(database).load(case.id)
    runner = Runner(database, case.id, tmp_path)
    result = Reproducer(runner, Clock()).run(
        incident, Inputs(database="jobs.sqlite", execution_id=case.id, max_attempts=case.max_attempts)
    )
    assert result.finding == "reproduced" and runner.calls == 4
    assert all(
        a.observation and a.observation.initial_state == incident.initial_state for a in result.attempts
    )


def test_query_parameters_cannot_select_other_jobs(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite"
    create_database(database)
    add_job(database, "one")
    add_job(database, "two", amounts=(900,))
    repository = SqliteIncidents(database)
    with pytest.raises(SnapshotUnavailable):
        repository.load("one' OR 1=1 --")
    assert len(repository.load("one").inputs.items) == 2
    assert len(repository.load("two").inputs.items) == 1


def test_unknown_schema_and_unsupported_yaml_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite"
    create_database(database)
    with closing(sqlite3.connect(database)) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(SnapshotUnavailable):
        SqliteIncidents(database).load("job")
    data = load_suite(SUITE_PATH).model_dump()
    data["command"] = "execute arbitrary shell"
    with pytest.raises(ValueError):
        Suite.model_validate(data)
    with pytest.raises(ValueError):
        Suite.model_validate({**data, "recipe": "unregistered-capability"})


def test_experiment_budget_is_not_reset_between_attempts(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite"
    create_database(database)
    add_job(database, "one", WorkerPlan(attempts=("clean",)))
    incident = SqliteIncidents(database).load("one")
    clock = Clock()

    class SlowRunner(Runner):
        def execute(self, incident: Incident, attempt: int, timeout: float) -> Observation:
            result = super().execute(incident, attempt, timeout)
            clock.now += 2
            return result

    runner = SlowRunner(database, "one", tmp_path)
    result = Reproducer(runner, clock).run(
        incident, Inputs(database="jobs.sqlite", execution_id="one", total_timeout_seconds=1.0)
    )
    assert result.finding == "inconclusive" and result.reason == "experiment_deadline"
    assert runner.calls == 1


def test_disposable_subprocesses_reproduce_without_mutating_source(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite"
    create_database(database)
    add_job(database, "one")
    incident = SqliteIncidents(database).load("one")
    result = Reproducer(FixtureRunner(PROJECT, database, "one"), SystemClock()).run(
        incident, Inputs(database="jobs.sqlite", execution_id="one", attempt_timeout_seconds=5.0)
    )
    assert result.finding == "reproduced" and len(result.attempts) == 2
    assert result.attempts[0].observation and result.attempts[1].observation
    assert result.attempts[0].observation.initial_state == result.attempts[1].observation.initial_state


def test_schemas_match_typed_contracts() -> None:
    directory = PROJECT / "scenarios/task_replay/workflow"
    for name, model in (("input", Inputs), ("output", Summary)):
        assert json.loads((directory / f"{name}.schema.json").read_text()) == model.model_json_schema()
    assert json.loads((directory.parent / "scenario.schema.json").read_text()) == Suite.model_json_schema()
    assert load_suite(directory.parent / "template.yaml").cases


def test_reproduction_policy_has_no_database_process_or_cli_dependencies() -> None:
    import ast

    directory = PROJECT / "scenarios/task_replay/workflow/reproduction"
    for name in ("service.py", "experiment.py", "contracts.py", "ports.py"):
        for node in ast.walk(ast.parse((directory / name).read_text())):
            assert not (isinstance(node, ast.Name) and node.id == "Any")
            if isinstance(node, ast.ImportFrom):
                assert not set((node.module or "").split(".")) & {
                    "sqlite3",
                    "subprocess",
                    "argparse",
                    "database",
                    "local",
                    "entrypoint",
                }
            if isinstance(node, ast.Import):
                assert not any(alias.name in {"sqlite3", "subprocess", "argparse"} for alias in node.names)


def test_database_path_cannot_escape_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        workspace_file(tmp_path, "../outside.sqlite")
    link = tmp_path / "escape"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        workspace_file(tmp_path, "escape/other.sqlite")
