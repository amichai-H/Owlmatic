"""Exercise a real subprocess test runner against a disposable fixture."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
    child = subprocess.run(
        [sys.executable, "fixture_test.py", "healthy" if inputs["healthy"] else "broken"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    print(child.stderr)
    passed = child.returncode == 0 and "Ran 2 tests" in child.stderr
    result = {
        "run_id": os.environ["OWLMATIC_RUN_ID"],
        "outcome": "pass" if passed else "fail",
        "checks": [{"name": "suite_passed", "status": "pass" if passed else "fail"}],
        "outputs": {"tests": 2, "suite_passed": passed},
        "effects": "none",
    }
    Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
