"""Mock database records and worker fault schedules, controlled outside the workflow bundle."""

import hashlib
import sqlite3
import sys
from contextlib import closing
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field

from .workflow.reproduction.contracts import Contract, Environment, Signature

PROJECT = Path(__file__).resolve().parents[2]
Behavior = Literal[
    "clean",
    "target_failure",
    "different_failure",
    "input_drift",
    "dirty_state",
    "lost_ack",
    "timeout",
    "bad_postcondition",
]
TARGET = Signature(stage="ledger.commit", exception="OptimisticLockError", code="REVISION_CONFLICT")


class WorkerPlan(Contract):
    attempts: tuple[Behavior, ...] = Field(default=("clean", "target_failure"), min_length=1, max_length=10)
    environment_drift: bool = False


def environment() -> Environment:
    digest = hashlib.sha256()
    for name in (
        "../__init__.py",
        "__init__.py",
        "fixtures.py",
        "worker.py",
        "workflow/__init__.py",
        "workflow/reproduction/__init__.py",
        "workflow/reproduction/contracts.py",
    ):
        digest.update(name.encode())
        digest.update((Path(__file__).parent / name).read_bytes())
    return Environment(
        worker_digest=digest.hexdigest(),
        runtime=f"cpython-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}-pydantic-{version('pydantic')}",
        configuration="mock-ledger-v1",
    )


def create_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        CREATE TABLE failed_jobs(execution_id TEXT PRIMARY KEY,task TEXT,account TEXT,expected_revision INTEGER,item_count INTEGER,worker_digest TEXT,runtime TEXT,configuration TEXT,stage TEXT,exception TEXT,code TEXT,replay_effects TEXT);
        CREATE TABLE input_items(execution_id TEXT,ordinal INTEGER,item_id TEXT,amount_cents INTEGER,PRIMARY KEY(execution_id,ordinal));
        CREATE TABLE account_snapshots(execution_id TEXT PRIMARY KEY,revision INTEGER,balance_cents INTEGER);
        CREATE TABLE simulation_plan(execution_id TEXT PRIMARY KEY,plan TEXT);
        PRAGMA user_version=1;
        """)
        db.commit()


def add_job(
    path: Path,
    execution_id: str,
    plan: WorkerPlan | None = None,
    *,
    amounts: tuple[int, ...] = (100, 250),
    revision: int = 7,
    external: bool = False,
) -> None:
    env = environment()
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "INSERT INTO failed_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                execution_id,
                "invoice.reconcile",
                f"account-{execution_id}",
                revision,
                len(amounts),
                env.worker_digest,
                env.runtime,
                env.configuration,
                TARGET.stage,
                TARGET.exception,
                TARGET.code,
                "external" if external else "local_only",
            ),
        )
        db.executemany(
            "INSERT INTO input_items VALUES (?,?,?,?)",
            ((execution_id, index, f"item-{index}", amount) for index, amount in enumerate(amounts)),
        )
        db.execute("INSERT INTO account_snapshots VALUES (?,?,?)", (execution_id, revision, 5000))
        db.execute(
            "INSERT INTO simulation_plan VALUES (?,?)",
            (execution_id, (plan or WorkerPlan()).model_dump_json()),
        )
        db.commit()


def read_plan(database: Path, execution_id: str) -> WorkerPlan:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT plan FROM simulation_plan WHERE execution_id=?", (execution_id,)).fetchone()
    if row is None:
        raise ValueError("Missing simulation plan")
    return WorkerPlan.model_validate_json(row[0])
