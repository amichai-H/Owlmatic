"""Subprocess runner simulation with job identity and observed artifact checks."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
    artifact = Path(os.environ["OWLMATIC_ARTIFACTS"]) / "runner-result.json"
    child = subprocess.run(
        [
            sys.executable,
            "runner_fixture.py",
            str(artifact),
            os.environ["OWLMATIC_RUN_ID"],
            "healthy" if inputs["healthy"] else "broken",
        ],
        check=False,
        timeout=5,
        capture_output=True,
    )
    observed = json.loads(artifact.read_text())
    checks = {
        "job_completed": child.returncode == 0,
        "job_identity": observed["job"] == os.environ["OWLMATIC_RUN_ID"],
        "artifact_verified": observed["revision"] == "fixture-v2",
    }
    passed = all(checks.values())
    result = {
        "run_id": os.environ["OWLMATIC_RUN_ID"],
        "outcome": "pass" if passed else "fail",
        "checks": [{"name": name, "status": "pass" if ok else "fail"} for name, ok in checks.items()],
        "outputs": {"artifact_verified": checks["artifact_verified"]},
        "effects": "completed",
    }
    Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
