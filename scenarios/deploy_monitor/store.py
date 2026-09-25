"""Transactional mock deployment storage shared by two independent HTTP processes."""

import sqlite3
import uuid
from pathlib import Path

from .fixtures import Plan, Record
from .workflow.monitoring.contracts import DeployRequest, Receipt


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS deployments (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL)"
            )

    def deploy(self, request: DeployRequest, plan: Plan, now: float) -> Receipt:
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT record FROM deployments WHERE request_key=?", (request.request_key,)
            ).fetchone()
            if row:
                record = Record.model_validate_json(row[0])
                if record.request != request:
                    raise ValueError("idempotency key conflicts with previous request")
                return record.receipt
            receipt = Receipt(
                deployment_id=uuid.uuid4().hex,
                service=request.service,
                version=request.version,
                replicas=("api-0", "api-1", "api-2"),
            )
            record = Record(receipt=receipt, request=request, plan=plan, accepted_at=now)
            connection.execute(
                "INSERT INTO deployments VALUES (?,?,?)",
                (receipt.deployment_id, request.request_key, record.model_dump_json()),
            )
            return receipt

    def get(self, deployment_id: str) -> Record:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT record FROM deployments WHERE id=?", (deployment_id,)).fetchone()
        if row is None:
            raise KeyError(deployment_id)
        return Record.model_validate_json(row[0])
