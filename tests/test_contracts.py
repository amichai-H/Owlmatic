import json

import pytest
from pydantic import ValidationError

from owlmatic.domain import CheckCounts, Run
from owlmatic.errors import OwlError
from tests.test_storage import invocation


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "pass"},
        {"finished_at": "finished"},
        {"outputs": {"ok": True}},
        {"duration_ms": -1},
        {"state": "completed"},
        {"state": "error", "finished_at": "finished"},
        {
            "state": "completed",
            "outcome": "pass",
            "finished_at": "finished",
            "checks": {"required": 1, "passed": 0, "failed": 0, "skipped": 1},
        },
    ],
)
def test_impossible_run_states_are_rejected(changes: dict[str, object]) -> None:
    raw = invocation(1).model_dump(mode="json")
    raw.update(changes)
    with pytest.raises(ValidationError):
        Run.model_validate_json(json.dumps(raw))


def test_terminal_transitions_are_rejected() -> None:
    terminal = invocation(1).finish(
        state="completed",
        outcome="pass",
        effects="completed",
        finished_at="finished",
        checks=CheckCounts(required=1, passed=1, failed=0, skipped=0),
    )
    with pytest.raises(OwlError) as caught:
        terminal.finish(
            state="completed",
            outcome="pass",
            effects="completed",
            finished_at="again",
            checks=terminal.checks,
        )
    assert caught.value.code == "INVALID_TRANSITION"
