"""Compare synthetic release metrics; never queries a production service."""

import json
import os
from pathlib import Path


def main() -> None:
    inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
    baseline = (200, 200, 200, 200, 500)
    release = (200, 200, 200, 200, 500) if inputs["healthy"] else (500, 500, 200, 500, 500)
    before = sum(code >= 500 for code in baseline)
    after = sum(code >= 500 for code in release)
    passed = len(release) >= 5 and after <= before
    result = {
        "run_id": os.environ["OWLMATIC_RUN_ID"],
        "outcome": "pass" if passed else "fail",
        "checks": [{"name": "no_regression", "status": "pass" if passed else "fail"}],
        "outputs": {"baseline_errors": before, "release_errors": after, "samples": len(release)},
        "effects": "none",
    }
    Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
