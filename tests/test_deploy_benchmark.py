"""Provider accounting and independent trial grading must also be tested."""

import json
from pathlib import Path

from scenarios.deploy_monitor.benchmark import Answer, oracle, read_usage
from scenarios.deploy_monitor.fixtures import Plan
from scenarios.deploy_monitor.store import Store
from scenarios.deploy_monitor.workflow.monitoring.contracts import DeployRequest


def test_usage_keeps_cache_and_reasoning_subsets_separate(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    events = [
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "item.completed", "item": {"type": "agent_message"}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 60,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
            },
        },
    ]
    path.write_text("\n".join(json.dumps(event) for event in events))
    usage, calls = read_usage(path)
    assert usage is not None
    assert usage.input_tokens + usage.output_tokens == 120
    assert usage.input_tokens - usage.cached_input_tokens == 40
    assert usage.reasoning_output_tokens == 5  # already inside output; do not add twice
    assert calls == 1


def test_missing_provider_usage_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"turn.failed"}\n')
    assert read_usage(path) == (None, 0)


def test_oracle_rejects_guessed_success_before_real_window(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    receipt = Store(database).deploy(
        DeployRequest(service="checkout", version="v2.4.0", request_key="one"), Plan(), 1000.0
    )
    answer = Answer(
        outcome="pass", deployment_id=receipt.deployment_id, observed_seconds=60.0, reason="claimed"
    )
    assert not oracle(answer, database, "healthy", 1.0, 1030.0)
    assert oracle(answer, database, "healthy", 1.0, 1062.0)


def test_oracle_rejects_error_before_it_could_have_arrived(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    receipt = Store(database).deploy(
        DeployRequest(service="checkout", version="v2.4.0", request_key="one"),
        Plan(fault="boundary_error", error_after_seconds=60.0, ingestion_delay_seconds=2.0),
        1000.0,
    )
    answer = Answer(
        outcome="fail", deployment_id=receipt.deployment_id, observed_seconds=60.0, reason="claimed"
    )
    assert not oracle(answer, database, "boundary_error", 1.0, 1061.0)
    assert oracle(answer, database, "boundary_error", 1.0, 1063.0)
