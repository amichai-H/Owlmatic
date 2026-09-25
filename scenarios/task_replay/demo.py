"""CLI adapter for YAML-defined reproduction scenarios."""

import argparse
from pathlib import Path

from .fixtures import PROJECT
from .suite import load_suite, run_suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path(__file__).with_name("scenarios.yaml"))
    parser.add_argument("--output", type=Path, default=PROJECT / ".owlmatic/task-replay")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    suite = load_suite(args.suite)
    if args.validate_only:
        print(f"Valid {suite.recipe} suite: {len(suite.cases)} cases")
        return
    report = run_suite(suite, args.output)
    print(report.model_dump_json(indent=2))
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
