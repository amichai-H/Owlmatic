"""A disposable mock job worker, never a production task or database mutation."""

import argparse
import hashlib
import time
from pathlib import Path

from .fixtures import TARGET, environment, read_plan
from .workflow.reproduction.contracts import AccountState, Environment, Incident, Observation, Signature


def describe(database: Path, execution_id: str) -> Environment:
    plan = read_plan(database, execution_id)
    env = environment()
    return Environment(
        worker_digest="0" * 64 if plan.environment_drift else env.worker_digest,
        runtime=env.runtime,
        configuration=env.configuration,
    )


def execute(database: Path, incident: Incident, attempt: int, scratch: Path) -> Observation | None:
    plan = read_plan(database, incident.original_execution)
    fault = plan.attempts[min(attempt - 1, len(plan.attempts) - 1)]
    if fault == "timeout":
        time.sleep(30)
    state_path = scratch / "account.json"
    if state_path.exists():
        raise ValueError("A replay cannot reuse prior attempt state")
    initial = AccountState(
        revision=incident.initial_state.revision + int(fault == "dirty_state"),
        balance_cents=incident.initial_state.balance_cents,
    )
    state_path.write_text(initial.model_dump_json())
    observed_revision = initial.revision + int(fault == "target_failure")
    signature = (
        TARGET
        if observed_revision != incident.inputs.expected_revision
        else Signature(stage="input.validate", exception="ValidationError", code="ACCOUNT_REJECTED")
        if fault == "different_failure"
        else None
    )
    final = (
        initial
        if signature
        else AccountState(
            revision=initial.revision + 1,
            balance_cents=initial.balance_cents
            + sum(item.amount_cents for item in incident.inputs.items)
            + int(fault == "bad_postcondition"),
        )
    )
    state_path.write_text(final.model_dump_json())
    if fault == "lost_ack":
        return None  # The local effect happened, but the caller receives no reliable acknowledgement.
    current = environment()
    return Observation(
        attempt=attempt,
        inputs_sha256="0" * 64
        if fault == "input_drift"
        else hashlib.sha256(incident.inputs.model_dump_json().encode()).hexdigest(),
        environment=current,
        initial_state=initial,
        final_state=final,
        state="failed" if signature else "succeeded",
        signature=signature,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--execution", required=True)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--incident", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--attempt", type=int)
    args = parser.parse_args()
    if args.describe:
        print(describe(args.database, args.execution).model_dump_json())
        return
    if args.incident is None or args.scratch is None or args.attempt is None:
        parser.error("Execution requires incident, scratch, and attempt")
    result = execute(
        args.database, Incident.model_validate_json(args.incident.read_bytes()), args.attempt, args.scratch
    )
    if result:
        print(result.model_dump_json())


if __name__ == "__main__":
    main()
