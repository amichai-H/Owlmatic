# Deployment monitoring: measured results and failures

**The reusable monitor works in the simulation. Owlmatic does not yet show a consistent
token advantage over calling that monitor directly.** The experiment exposed an execution
guidance problem serious enough to erase the savings from automation.

Run the [lab](../scenarios/deploy_monitor/README.md) to reproduce the scenario. Machine-readable
measurements, including failed attempts, are in [the dataset](deploy-monitor-measurements.json).

## What ran

On 2026-09-25, Codex CLI 0.156.1 ran `gpt-6-luna` with low reasoning effort in fresh workspaces.
Each trial had separate deployment/log HTTP processes and a separate SQLite fixture database.
The observation window was **60 real seconds**, with a five-second ingestion grace. The
boundary-error fixture delayed ingestion by two seconds. Claude Code reported no authenticated
session, so these are not Claude measurements.

The manual agent received an API contract and could write helper programs. The direct arm
called the reviewed monitor once. The Owlmatic arm discovered and ran the same monitor,
then inspected it. All arms were allowed to choose efficient execution strategies. Fault plans
were outside agent workspaces; the task did not reveal expected outcomes.

The provider's `turn.completed.usage` counters supply the measurements. Total tokens here are
**input + output**, including cached input. Reasoning output is already a subset of output.
This is not a dollar calculation. The [Codex non-interactive interface](https://developers.openai.com/codex/noninteractive)
provides the event stream used by the harness.

## Original and environment-corrected trials

| Case | Approach | Total tokens | Uncached input | Result |
|---|---|---:|---:|---|
| Healthy | Manual | 163,499 | 26,016 | Pass; fixture oracle accepted |
| Healthy | Direct monitor | 98,062 | 20,089 | Pass; fixture oracle accepted |
| Healthy | Owlmatic, wrong interpreter | 66,339 | 19,449 | Inconclusive; no deployment evidence; rejected |
| Healthy | Owlmatic, environment corrected | 516,338 | 42,918 | Pass; retry/recovery spiral |
| Delayed boundary error | Manual | 111,645 | 23,321 | Fail; error correctly detected |
| Delayed boundary error | Direct monitor | 98,339 | 19,285 | Fail; error correctly detected |
| Delayed boundary error | Owlmatic | 83,485 | 20,487 | Fail; error correctly detected |
| Missing logs | Manual | 189,037 | 26,735 | Inconclusive; fixture oracle accepted |
| Missing logs | Direct monitor | 178,379 | 15,102 | Inconclusive; fixture oracle accepted |
| Missing logs | Owlmatic | 369,406 | 33,862 | Inconclusive, but lost deployment/run evidence; rejected |

No failed attempt is counted as a saving. The fixture oracle checks the reported outcome,
deployment identity/version/service and earliest possible finish time. It does not certify
that generated verifier code generalizes: review of the healthy manual script found it derived
expected replica names from its first status response instead of preserving the controller's
deployment receipt. The healthy fixture did not expose that weakness.

## Follow-up improvement

The original Owlmatic guidance let a long `run` command outlive the host tool's initial response.
The agent issued another run rather than collecting the existing command's result, hit `BUSY`,
and explored unrelated recovery/status commands. In one trial it eventually started a second
monitor invocation. The mock controller's request key prevented a second deployment; it did
not prevent duplicate Owlmatic monitoring work.

The shared skill now recommends an immediate run ID (`--wait 0`), a stable Owlmatic request ID,
and collecting a pending host command before issuing another invocation. The follow-up supplied
the updated skill; it is a **different treatment**, not a replacement for the failed samples.

| Case | Revised Owlmatic tokens | Uncached input | Result |
|---|---:|---:|---|
| Healthy | 103,472 | 6,098 | Pass; fixture oracle accepted |
| Missing logs | 121,481 | 7,886 | Inconclusive with deployment evidence; fixture oracle accepted |

For the healthy case, revised Owlmatic used **36.7% fewer total tokens than manual**, but
**5.5% more than the direct monitor**. The direct monitor already saved 40.0% against manual.
These observations support automation; they do not establish a defensible token-saving layer
beyond automation. Cache conditions strongly favored the later follow-up, so its much lower
uncached input cannot be attributed to Owlmatic or converted into a monetary saving.

## Verification beyond agent answers

Three standalone Owlmatic demonstrations produced pass, detected-error fail, and missing-log
inconclusive after real one-minute windows. The healthy run checked 180 heartbeat events;
the delayed-error run checked 181 events. No logs produced zero events and an inconclusive
result, never pass. Their summaries were exported to the local dashboard **without a savings
baseline**; these host measurements are retained separately from estimated dashboard totals.

The repository now has 168 passing tests, including 37 new monitor/accounting tests. The
additional nine-case live HTTP matrix covers healthy operation, delayed errors, bad versions,
missing replicas, missing/gapped logs, cursor loops, malformed JSON contracts and unavailable
services. Strict mypy, Ruff, formatting and wheel/sdist builds pass locally. CI runs the HTTP
matrix on Linux/macOS and Python 3.11–3.14.

Tests also demonstrate a deliberately naive false pass: checking only that an empty log
response contains no error succeeds while the completeness watermark proves the window
was not covered. The monitor refuses that result.

## What still needs improvement

1. **Runtime binding and preflight.** An installed CLI does not guarantee the workflow's
   `python3` resolves to the right interpreter or dependencies. The lab explicitly selects its
   environment; production needs a reviewed runtime/dependency contract and early validation.
2. **Recoverable operation handles.** Stable request IDs should be easier to carry across host
   tool lifecycles. A structured `BUSY` response identifying the owning invocation, and lookup
   by request ID, would reduce recovery exploration. A skill improvement is not a durable API fix.
3. **Completion delivery.** Long workflows should not need repeated reasoning about waiting.
   Measure host completion notifications or a deterministic waiting adapter before building
   another orchestration server. Keep polling out of the model where the host permits it.
4. **Real log guarantees.** The mock explicitly certifies completeness. Production log systems
   may not. Sampling and heartbeat logs alone cannot prove absence of every transient error or
   version change. Require the provider's ingestion and deployment-event contracts.
5. **A stronger study.** Repeat each condition, randomize order, pin runtime/source/host context,
   test both Codex and Claude, and include recovery and maintenance costs. The exploratory run
   included a rollout-timeout fix between cases and non-randomized follow-ups. Within-arm
   variation is unknown. Do not promote these numbers as an organization-wide percentage.

If one-time authoring/review/setup costs are `C` tokens and measured per-invocation savings are
`M - O`, token break-even is `ceil(C / (M - O))` only when that difference is positive and
correctness is comparable. `C` was not measured here. Against a direct script, the healthy
follow-up has a negative difference, so this sample provides **no token break-even** for the
extra Owlmatic layer. Its case must rest on discovery, reviewed execution, idempotency and
retained evidence—and those features must survive the host behavior exposed by this test.
