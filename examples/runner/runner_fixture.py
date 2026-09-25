import json
import sys
from pathlib import Path

if __name__ == "__main__":
    output, job, scenario = sys.argv[1:]
    Path(output).write_text(
        json.dumps({"job": job, "revision": "fixture-v2" if scenario == "healthy" else "stale"})
    )
