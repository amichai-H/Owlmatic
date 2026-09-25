"""Bootstrap and independently inspect a disposable local SQLite database."""

import json
import os
import sqlite3
from pathlib import Path


def main() -> None:
    inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
    database = Path(os.environ["OWLMATIC_WORKSPACE"]) / "owlmatic-example.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version VALUES (?)", (1 if inputs["healthy"] else 0,))
    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
    passed = version == 1
    result = {
        "run_id": os.environ["OWLMATIC_RUN_ID"],
        "outcome": "pass" if passed else "fail",
        "checks": [{"name": "schema_ready", "status": "pass" if passed else "fail"}],
        "outputs": {"schema_version": version, "schema_ready": passed},
        "effects": "completed",
    }
    Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
