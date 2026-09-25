"""Composition and Owlmatic evidence serialization; no experiment decisions here."""

import os
from pathlib import Path
from typing import Literal

from owlmatic.domain import Check, Evidence

from .contracts import Inputs
from .database import SqliteIncidents
from .experiment import Experiment
from .local import FixtureRunner, LocalEvidence, SystemClock, workspace_file
from .service import Reproducer


def main() -> None:
    options = Inputs.model_validate_json(Path(os.environ["OWLMATIC_INPUT"]).read_bytes())
    workspace = Path(os.environ["OWLMATIC_WORKSPACE"])
    database = workspace_file(workspace, options.database)
    experiment = Experiment(
        SqliteIncidents(database),
        Reproducer(FixtureRunner(workspace, database, options.execution_id), SystemClock()),
        LocalEvidence(Path(os.environ["OWLMATIC_ARTIFACTS"])),
    )
    result = experiment.run(options)
    outcome: Literal["pass", "fail", "inconclusive"] = (
        "fail"
        if result.finding == "reproduced"
        else "pass"
        if result.finding == "not_reproduced"
        else "inconclusive"
    )
    status: Literal["pass", "fail", "skip"] = (
        "fail" if outcome == "fail" else "pass" if outcome == "pass" else "skip"
    )
    evidence = Evidence(
        run_id=os.environ["OWLMATIC_RUN_ID"],
        outcome=outcome,
        checks=(Check(name="target_failure_absent_in_bounded_replay", status=status),),
        outputs=result.model_dump(mode="json"),
        effects="none",
    )
    Path(os.environ["OWLMATIC_RESULT"]).write_text(evidence.model_dump_json())


if __name__ == "__main__":
    main()
