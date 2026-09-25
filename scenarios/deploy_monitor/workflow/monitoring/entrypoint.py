"""Composition and evidence translation only; identical service for direct and Owlmatic runs."""

import argparse
import os
from pathlib import Path
from typing import Literal

from owlmatic.domain import Check, Evidence

from .contracts import Inputs, Result
from .http import HTTPDeployments, HTTPLogs, LocalHTTP, SystemClock
from .service import Monitor


def execute(inputs: Inputs) -> Result:
    return Monitor(
        HTTPDeployments(LocalHTTP(inputs.deploy_url)),
        HTTPLogs(LocalHTTP(inputs.logs_url)),
        SystemClock(),
    ).run(inputs)


def main() -> None:
    if "OWLMATIC_INPUT" in os.environ:
        inputs = Inputs.model_validate_json(Path(os.environ["OWLMATIC_INPUT"]).read_bytes())
        result = execute(inputs)
        check_status: Literal["pass", "fail", "skip"] = (
            "pass" if result.outcome == "pass" else "fail" if result.outcome == "fail" else "skip"
        )
        evidence = Evidence(
            run_id=os.environ["OWLMATIC_RUN_ID"],
            outcome=result.outcome,
            checks=(Check(name="deployment_window", status=check_status),),
            outputs=result.model_dump(mode="json"),
            effects=result.effects,
        )
        Path(os.environ["OWLMATIC_RESULT"]).write_text(evidence.model_dump_json())
    else:
        parser = argparse.ArgumentParser(description="Run the local deploy-monitor simulation")
        parser.add_argument("inputs", type=Path)
        args = parser.parse_args()
        print(execute(Inputs.model_validate_json(args.inputs.read_bytes())).model_dump_json())


if __name__ == "__main__":
    main()
