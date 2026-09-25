"""Execution lifecycle and evidence policy, with injected process and persistence boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from .domain import Check, Evidence, Failure, Job, Run
from .errors import OwlError
from .policy import ExecutionPolicy, environment_for
from .ports import ArtifactStore, Clock, LogSink, ProcessRequest, RunGuard, RunRepository, WorkflowProcess
from .recovery_service import RecoveryService
from .verification import verify_evidence


def clean_evidence(evidence: Evidence, secrets: tuple[str, ...]) -> Evidence:
    def text(value: str) -> str:
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def clean(value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            return text(value)
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {text(k): clean(v) for k, v in value.items()}
        return value

    outputs = {text(k): clean(v) for k, v in evidence.outputs.items()}
    checks = tuple(Check(name=text(check.name), status=check.status) for check in evidence.checks)
    return Evidence(
        run_id=evidence.run_id,
        outcome=evidence.outcome,
        checks=checks,
        outputs=outputs,
        effects=evidence.effects,
    )


@dataclass(frozen=True)
class RunObserver:
    run_id: str
    owner: str
    artifacts: ArtifactStore
    runs: RunRepository
    sink: LogSink

    def output(self, stream: str, data: bytes, final: bool = False) -> None:
        self.sink.write(stream, data, final)

    def cancelled(self) -> bool:
        return self.artifacts.cancelled(self.run_id)

    def heartbeat(self) -> None:
        self.runs.heartbeat(self.run_id, self.owner)


@dataclass(frozen=True)
class Supervisor:
    policy: ExecutionPolicy
    artifacts: ArtifactStore
    runs: RunRepository
    process: WorkflowProcess
    clock: Clock
    guard: RunGuard
    recovery: RecoveryService
    new_owner: Callable[[], str]

    def execute(self, job: Job) -> Run:
        with self.guard.hold(job.run.run_id):
            owner = self.new_owner()
            self.runs.claim(job.run.run_id, owner)
            result = self._execute(job, owner)
            # SQLite commits the terminal state and recovery intent together.
            # Stale workers cannot update the authoritative result or release locks.
            self.runs.finish(result, owner)
        self.recovery.finalize(result.run_id)
        return result

    def _execute(self, job: Job, owner: str) -> Run:
        start = self.clock.monotonic()
        run = job.run
        sink: LogSink | None = None
        launched = False
        try:
            p = self.policy.check(
                job.workflow,
                job.inputs,
                job.profile,
                run.target.environment,
                Path(run.target.workspace),
                validation=job.validation,
            )
            selected = environment_for(job.workflow, p, self.policy.host.environment)
            directory = self.artifacts.directory(run.run_id)
            environment = {
                **selected.values,
                "OWLMATIC_INPUT": str(directory / "inputs.json"),
                "OWLMATIC_RESULT": str(directory / "raw-result.json"),
                "OWLMATIC_RUN_ID": run.run_id,
                "OWLMATIC_WORKSPACE": run.target.workspace,
                "OWLMATIC_ENVIRONMENT": run.target.environment,
                "OWLMATIC_ARTIFACTS": str(directory / "artifacts"),
            }
            command = list(job.workflow.manifest.entrypoint)
            if "/" in command[0]:
                command[0] = str(job.workflow.directory / command[0])
            sink = self.artifacts.log(run.run_id, selected.secrets, p.max_log_bytes)
            observer = RunObserver(run.run_id, owner, self.artifacts, self.runs, sink)
            launched = True
            code = self.process.execute(
                ProcessRequest(
                    argv=command,
                    directory=job.workflow.directory,
                    environment=environment,
                    timeout_seconds=min(
                        p.max_timeout_seconds, job.workflow.manifest.execution.timeout_seconds
                    ),
                    max_output_bytes=p.max_log_bytes,
                    run_id=run.run_id,
                ),
                observer,
            )
            evidence = self.artifacts.raw_evidence(run.run_id)
            checks = verify_evidence(evidence, job.workflow, run.run_id, code, self.policy.schemas)
            clean = clean_evidence(evidence, selected.secrets)
            self.artifacts.save_evidence(clean)
            run = run.finish(
                state="completed",
                outcome=evidence.outcome,
                effects=evidence.effects,
                checks=checks,
                outputs=clean.outputs,
                finished_at=self.clock.iso(),
                duration_ms=round((self.clock.monotonic() - start) * 1000),
            )
        except OwlError as error:
            run = run.finish(
                state="cancelled" if error.code == "CANCELLED" else "error",
                outcome="inconclusive",
                error=Failure(code=error.code, message=str(error)),
                log_truncated=error.code == "OUTPUT_LIMIT",
                effects=run.effects if launched else "none",
                finished_at=self.clock.iso(),
                duration_ms=round((self.clock.monotonic() - start) * 1000),
            )
        except Exception:
            # Exception reprs can contain inputs or credential values.
            run = run.finish(
                state="error",
                outcome="inconclusive",
                effects=run.effects if launched else "none",
                error=Failure(
                    code="EXECUTION_ERROR", message="Execution or evidence decoding failed; inspect logs"
                ),
                finished_at=self.clock.iso(),
                duration_ms=round((self.clock.monotonic() - start) * 1000),
            )
        finally:
            if sink:
                sink.close()
        return run
