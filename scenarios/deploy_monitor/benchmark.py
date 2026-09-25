"""Opt-in paid/account-metered agent trials. Never substitute bytes for provider tokens."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import Field

from owlmatic.bootstrap import create_application
from owlmatic.domain import Catalog, SearchRequest

from .fixtures import Fault, Plan
from .runtime import PROJECT, servers, stop
from .store import Store
from .workflow.monitoring.contracts import Contract, Inputs

Arm = Literal["manual", "direct", "owlmatic"]


class Answer(Contract):
    outcome: Literal["pass", "fail", "inconclusive"]
    deployment_id: str | None
    observed_seconds: float
    reason: str


class Usage(Contract):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)


class Trial(Contract):
    arm: Arm
    fault: Fault
    model: str
    usage: Usage | None
    answer: Answer | None
    oracle_correct: bool
    elapsed_seconds: float
    tool_calls: int
    error: str | None
    artifacts: str


COMMON = """Deploy the service and version in inputs.json using the local mock deploy API.
Verify ALL expected replicas reach and retain that version and readiness, and prove no errors
for monitor_minutes * 60 real seconds AFTER readiness. Incomplete/missing log coverage is
inconclusive, never pass. Wait for late ingestion up to ingestion_grace_seconds; errors whose
event timestamp falls at the window boundary count. Do not roll back or redeploy on failure.
Use request_key for idempotency. Return the requested JSON result only after verification.
Use only workspace files, the documented APIs, and the supplied command, if any. Do not read
source implementations, fault plans, other trials, or unrelated files. Do not access external
services. Do not invent observations. You may write helper programs; choose efficient tools.
"""

PROTOCOL = """Local API protocol (no credentials; HTTP JSON):
POST deploy_url/deployments with {service, version, request_key}. Returns
{deployment_id, service, version, replicas:[expected replica names]}.
GET deploy_url/deployments/{deployment_id} returns
{deployment_id, observed_at:Unix seconds, replicas:[{name,version,ready:bool}]}.
GET logs_url/logs?deployment_id=...&start=...&end=...&cursor=0 returns
{deployment_id,start,end,complete_through,gap,events:[{sequence,at,replica,version,
level:'heartbeat'|'error'}],next_cursor:int|null}. Follow all pages for the fixed interval.
start/end are inclusive event-time bounds. A watermark complete_through >= end certifies
complete delivery through end ONLY after reading all pages. gap=true invalidates coverage.
Require matching response identity/bounds, unique advancing event sequences and cursors,
one stable watermark across pages and monotonic watermarks across polls. Events must belong
to expected replicas, have the expected version, and lie within the requested interval.
Every replica emits a heartbeat each second. Require at least one per replica and no gap
over 2.5 seconds, including window edges. Missing/stale streams may be empty: not success.
Use a monotonic local clock for elapsed time. Anchor the event-time window to observed_at
when all replicas first become ready. Check status during monitoring; detect source clock
jumps/staleness exceeding 2 seconds. Final window end is fixed at start + requested duration.
Bound HTTP timeouts (2 seconds), pagination (128 pages), rollout wait and ingestion grace.
HTTP/malformed/identity/coverage failures => inconclusive. Known version/readiness regression,
rollout timeout, or errors => fail. No external dependencies are needed to call these APIs.
"""


def read_usage(path: Path) -> tuple[Usage | None, int]:
    usage: Usage | None = None
    calls = 0
    for line in path.read_text().splitlines():
        value = json.loads(line)  # genuine versioned host event boundary
        if value.get("type") == "turn.completed":
            usage = Usage.model_validate(value["usage"])
        if value.get("type") == "item.completed" and value.get("item", {}).get("type") == "command_execution":
            calls += 1
    return usage, calls


def oracle(answer: Answer | None, database: Path, fault: Fault, minutes: float, finished: float) -> bool:
    if answer is None or answer.deployment_id is None:
        return False
    try:
        record = Store(database).get(answer.deployment_id)
    except KeyError:
        return False
    expected = "pass" if fault == "healthy" else "fail" if fault == "boundary_error" else "inconclusive"
    if (
        answer.outcome != expected
        or record.receipt.version != "v2.4.0"
        or record.receipt.service != "checkout"
    ):
        return False
    earliest_finish = record.accepted_at + record.plan.rollout_seconds + minutes * 60
    if fault == "boundary_error":
        earliest_finish += record.plan.ingestion_delay_seconds
    if finished < earliest_finish:
        return False
    if expected == "pass":
        return (
            finished - record.accepted_at >= record.plan.rollout_seconds + minutes * 60
            and answer.observed_seconds >= minutes * 60
        )
    return True


def run_trial(
    output: Path, arm: Arm, fault: Fault, minutes: float, model: str, revised: bool = False
) -> Trial:
    directory = output / f"{fault}-{arm}-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    # A fresh directory outside the checkout avoids giving the manual arm a prebuilt solution.
    workspace = Path(tempfile.mkdtemp(prefix="owlmatic-agent-trial-"))
    plan = Plan(
        fault=fault,
        error_after_seconds=minutes * 60,
        ingestion_delay_seconds=2.0 if fault == "boundary_error" else 0.0,
    )
    answer: Answer | None = None
    error: str | None = None
    with servers(directory / "servers", plan) as endpoints:
        inputs = Inputs(
            deploy_url=endpoints.deploy,
            logs_url=endpoints.logs,
            service="checkout",
            version="v2.4.0",
            request_key=uuid.uuid4().hex,
            monitor_minutes=minutes,
        )
        (workspace / "inputs.json").write_text(inputs.model_dump_json(indent=2))
        (directory / "workspace.txt").write_text(str(workspace))
        (directory / "answer.schema.json").write_text(json.dumps(Answer.model_json_schema()))
        env = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        }
        prompt = COMMON
        if arm == "manual":
            (workspace / "API.md").write_text(PROTOCOL)
            prompt += "Read API.md for the API contract and implement verification yourself.\n"
        elif arm == "direct":
            direct_command = (
                f"PYTHONPATH={shlex.quote(str(PROJECT))} {shlex.quote(sys.executable)} "
                "-m scenarios.deploy_monitor.workflow.monitoring.entrypoint inputs.json"
            )
            prompt += (
                "An installed, reviewed monitor implements this contract. Run it and report its result:\n"
                + direct_command
            )
        else:
            env["OWLMATIC_HOME"] = str(workspace / "home")
            app = create_application(workspace / "home")
            app.catalog.add(Catalog(name="lab", source=str(PROJECT / "scenarios/deploy_monitor")))
            app.catalog.trust(app.catalog.find(SearchRequest(query="deploy monitor")).results[0].ref)
            prefix = f"PATH={shlex.quote(str(Path(sys.executable).parent))}:$PATH "
            prompt += (
                "For every owlmatic invocation, prefix the command with "
                + prefix
                + "to select the installed Python environment; login shells may reset PATH.\n"
            )
            prompt += """A trusted workflow is installed. Discover it with `owlmatic find 'deploy monitor' --json`.
Run its returned ref using `owlmatic run REF --input-file inputs.json --environment simulation --json`.
If running, use `owlmatic inspect RUN_ID --wait 20 --json` until terminal. Inspect evidence if needed.
Report the verified workflow result. No need to read its implementation.
"""
            if revised:
                prompt += (
                    "\nUpdated invocation guidance:\n" + (PROJECT / "skills/owlmatic/SKILL.md").read_text()
                )
                prompt += f"\nFor this operation use --request-id {inputs.request_key} --wait 0 on the run command.\n"
        (directory / "prompt.txt").write_text(prompt)
        command = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=true",
            "-c",
            'model_reasoning_effort="low"',
            "-m",
            model,
            "--json",
            "-C",
            str(workspace),
            "--output-schema",
            str(directory / "answer.schema.json"),
            "--output-last-message",
            str(directory / "answer.json"),
            "-",
        ]
        started = time.monotonic()
        with (
            (directory / "events.jsonl").open("wb") as events,
            (directory / "stderr.log").open("wb") as stderr,
        ):
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=events, stderr=stderr, env=env)
            try:
                process.communicate(prompt.encode(), timeout=480)
                if process.returncode:
                    error = f"host exited {process.returncode}"
            except subprocess.TimeoutExpired:
                error = "host exceeded 480-second trial budget"
            finally:
                stop(process)
        elapsed = time.monotonic() - started
        usage, calls = read_usage(directory / "events.jsonl")
        try:
            answer = Answer.model_validate_json((directory / "answer.json").read_bytes())
        except (OSError, ValueError):
            error = error or "missing or invalid host answer"
        result = Trial(
            arm=arm,
            fault=fault,
            model=model,
            usage=usage,
            answer=answer,
            oracle_correct=oracle(answer, endpoints.database, fault, minutes, time.time()),
            elapsed_seconds=elapsed,
            tool_calls=calls,
            error=error,
            artifacts=str(directory),
        )
        (directory / "trial.json").write_text(result.model_dump_json(indent=2))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=1.0)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--revised-guidance", action="store_true")
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=("manual", "direct", "owlmatic"),
        default=["manual", "direct", "owlmatic"],
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=("healthy", "boundary_error", "missing_logs"),
        default=["healthy", "boundary_error", "missing_logs"],
    )
    parser.add_argument("--output", type=Path, default=PROJECT / ".owlmatic/deploy-monitor/benchmark")
    args = parser.parse_args()
    results: list[Trial] = []
    # Concurrency reduces wall time, not charged tokens. Each trial has independent APIs and state.
    for case in args.cases:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(
                    run_trial,
                    args.output.resolve(),
                    arm,
                    case,
                    args.minutes,
                    args.model,
                    args.revised_guidance,
                )
                for arm in args.arms
            ]
            for future in futures:
                result = future.result()
                results.append(result)
                print(result.model_dump_json(), flush=True)
    (args.output / "trials.json").write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2)
    )


if __name__ == "__main__":
    main()
