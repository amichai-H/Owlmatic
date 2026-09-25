"""Collect three synthetic log sources, checking completeness and error count."""

import json
import os
from pathlib import Path


def main() -> None:
    inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
    directory = Path(os.environ["OWLMATIC_ARTIFACTS"])
    for source in ("api", "worker", "database"):
        level = "ERROR" if source == "worker" and not inputs["healthy"] else "INFO"
        (directory / f"{source}.log").write_text(f"{level} request=fixture-1 source={source}\n")
    files = sorted(directory.glob("*.log"))
    combined = "".join(path.read_text() for path in files)
    errors = combined.count("ERROR")
    (directory / "combined.txt").write_text(combined)
    print(combined)
    observations = {"sources_complete": len(files) == 3, "no_errors": errors == 0}
    result = {
        "run_id": os.environ["OWLMATIC_RUN_ID"],
        "outcome": "pass" if all(observations.values()) else "fail",
        "checks": [{"name": name, "status": "pass" if ok else "fail"} for name, ok in observations.items()],
        "outputs": {"sources": len(files), "errors": errors},
        "effects": "completed",
    }
    Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
