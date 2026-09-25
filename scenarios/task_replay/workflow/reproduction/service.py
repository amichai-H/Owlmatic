"""One pinned experiment, with explicit stop conditions and no diagnosis heuristics."""

import hashlib
from dataclasses import dataclass

from .contracts import Attempt, Incident, Inputs, ReplayUnavailable, Result, Summary
from .ports import Clock, Runner


def input_digest(incident: Incident) -> str:
    return hashlib.sha256(incident.inputs.model_dump_json().encode()).hexdigest()


def summarize(result: Result) -> Summary:
    return Summary(
        finding=result.finding,
        reason=result.reason,
        attempts=len(result.attempts),
        clean_attempts=sum(a.finding == "clean" for a in result.attempts),
        matching_failures=sum(a.finding == "target_failure" for a in result.attempts),
    )


@dataclass(frozen=True)
class Reproducer:
    runner: Runner
    clock: Clock

    def run(self, incident: Incident, options: Inputs) -> Result:
        if incident.replay_effects != "local_only":
            return Result(finding="blocked", reason="external_effects_require_an_isolated_adapter")
        deadline = self.clock.monotonic() + options.total_timeout_seconds
        try:
            environment = self.runner.describe(
                min(options.attempt_timeout_seconds, options.total_timeout_seconds)
            )
        except ReplayUnavailable:
            return Result(finding="inconclusive", reason="environment_unavailable")
        if environment != incident.environment:
            return Result(finding="blocked", reason="environment_does_not_match_incident")
        attempts: list[Attempt] = []
        for number in range(1, options.max_attempts + 1):
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return Result(finding="inconclusive", reason="experiment_deadline", attempts=tuple(attempts))
            try:
                observation = self.runner.execute(
                    incident, number, min(remaining, options.attempt_timeout_seconds)
                )
            except ReplayUnavailable:
                attempts.append(Attempt(number=number, finding="unknown"))
                return Result(
                    finding="inconclusive",
                    reason="execution_unknown_no_automatic_retry",
                    attempts=tuple(attempts),
                )
            if self.clock.monotonic() > deadline:
                attempts.append(Attempt(number=number, finding="unknown", observation=observation))
                return Result(finding="inconclusive", reason="experiment_deadline", attempts=tuple(attempts))
            if (
                observation.attempt != number
                or observation.environment != incident.environment
                or observation.inputs_sha256 != input_digest(incident)
                or observation.initial_state != incident.initial_state
            ):
                attempts.append(Attempt(number=number, finding="incomparable", observation=observation))
                return Result(
                    finding="inconclusive", reason="replay_conditions_changed", attempts=tuple(attempts)
                )
            if observation.state == "failed":
                matches = observation.signature == incident.failure
                attempts.append(
                    Attempt(
                        number=number,
                        finding="target_failure" if matches else "different_failure",
                        observation=observation,
                    )
                )
                return Result(
                    finding="reproduced" if matches else "inconclusive",
                    reason="matching_failure_observed" if matches else "different_failure_requires_diagnosis",
                    attempts=tuple(attempts),
                )
            if (
                observation.final_state.revision != incident.initial_state.revision + 1
                or observation.final_state.balance_cents
                != incident.initial_state.balance_cents
                + sum(item.amount_cents for item in incident.inputs.items)
            ):
                attempts.append(Attempt(number=number, finding="incomparable", observation=observation))
                return Result(
                    finding="inconclusive", reason="success_postcondition_failed", attempts=tuple(attempts)
                )
            attempts.append(Attempt(number=number, finding="clean", observation=observation))
        return Result(
            finding="not_reproduced",
            reason="bounded_clean_attempts_do_not_prove_fixed",
            attempts=tuple(attempts),
        )
