# Measuring executable memory

Owlmatic imports usage from explicitly selected local Codex and Claude Code histories. No model call, agent wrapper, mandatory server, or paid benchmark is required. A workflow run by itself does **not** establish token savings: comparison needs a compatible manual task and the agent's measured reuse task.

## Enable collection

Copy [the YAML example](../config/measurement.example.yaml), set `measurement.enabled: true`, and apply it:

```sh
owlmatic export configure --file ./measurement.yaml
```

This configures observability even when network export remains disabled. Collection is opt-in. Paths must identify individual files; there is no automatic directory scan. The watcher is foreground and must be started explicitly; changing YAML does not install a background service.

## Record a baseline and reuse

Import the original manual task, using the actual host, model, and a workload label that describes comparable inputs and acceptance criteria:

```sh
owlmatic measure import --host codex --session /path/to/manual.jsonl \
  --task-id manual-deploy-1 --purpose manual --model exact-model \
  --workload deploy-60s-3-replicas --verified
owlmatic measure baseline '<exact-workflow-ref>' --task manual-deploy-1

owlmatic measure import --host codex --session /path/to/reuse.jsonl \
  --task-id reuse-deploy-1 --model exact-model --workload deploy-60s-3-replicas
owlmatic measure report '<exact-workflow-ref>' --json
```

`--verified` is a human attestation for a manual baseline, not an independent correctness oracle. Baselines must include the full manual task, including discovery, retries, and result interpretation. For reuse, Owlmatic extracts run IDs from visible JSON tool results and checks them against the local run ledger. Use repeatable `--run-id` when automatic correlation is unavailable. Reuse verification cannot be asserted with `--verified`: runs must have definitive outcomes and known effects. Failed attempts incur costs. Multi-workflow tasks stay unattributed rather than charging the full task separately to each workflow. Unattributed non-manual tasks suppress aggregate savings.

Use `--first-line` and `--last-line` to select one complete task from a longer history, including its start and completion. Overlapping selections, copied completed histories, run reassignment, and changed completed histories are rejected. Stable task IDs allow incomplete imports to be completed later without double counting. Multi-task sessions and mixed models cannot establish a baseline. Model overrides that conflict with recorded metadata are rejected as incomplete evidence.

Supported shapes: Codex exec JSONL (`item.completed` and `turn.completed`), native Codex events with cumulative usage and task boundaries, and Claude Code JSONL assistant messages, tool-result messages, and final result summaries. Repeated message IDs are deduplicated. Unsupported partial streaming or subagent usage is flagged incomplete. Claude session-wide final summaries cannot be sliced into tasks. New host formats require an adapter update; unknown evidence never becomes zero.

## Watch for late usage

Add exact session selections to `measurement.sources` in YAML, then run:

```sh
owlmatic measure watch --once --json
owlmatic measure watch --json
```

Each configured source has a stable task ID and workload/model metadata. The watcher reads selected local files, updates incomplete records, prunes expired evidence, and reports safe error codes. It does not discover new sessions automatically or split conversations into tasks. Use a dedicated single-task history or explicit complete line ranges. An append that introduces another task is not merged into a previously completed observation.

Retention is enforced by the watcher, including `--once`. Schedule that command if you do not keep the watcher running. Stop a foreground watcher with Ctrl+C.

In `export.mode: after_workflow`, measurement imports, baseline links, coverage changes, and watcher updates also trigger exports. This handles usage that arrives after the workflow finishes. In manual mode, run `owlmatic export push` after importing. Disabling export preserves local measurement configuration.

## Accounting

Provider input and output are counted once. Codex cached input is a subset of input. Claude uncached input, cache-read input, and cache-write input are normalized into total input. Reported reasoning output is a subset of output. Missing optional categories remain unknown. These totals are token volume, not prices; cached and uncached tokens do not have equal monetary cost.

For each complete reuse task, candidate manual baselines must match host, exact model, declared workload, and the linked immutable workflow reference:

```text
operational estimate = sum(verified task ? minimum matching manual total : 0)
                     - sum(actual reuse task totals)
net estimate         = operational estimate - creation tokens - maintenance tokens
```

The minimum is an observed historical sample, **not a guaranteed lower bound on future savings**. Sample count and observed range are retained. Results stay preliminary; a one-sample comparison is not a causal experiment. Negative values are preserved. Missing task usage or baseline prevents an operational total. Actual measured usage remains available with explicit coverage.

Record creation and maintenance sessions using `--purpose creation` or `--purpose maintenance` and `--workflow-ref`. Declare completeness only after all such costs have been imported:

```sh
owlmatic measure coverage '<exact-ref>' --setup-complete --maintenance-complete
```

These flags attest coverage. If a category has no records, declaring it complete explicitly asserts zero recorded token cost. Do not use the flags just to populate a dashboard card. Without complete coverage, net savings remains unknown. Retention that removes associated evidence invalidates cost coverage. Every changed workflow version needs its own baseline and coverage; reports do not inherit them silently.

Visible tool-context reduction is separate from provider usage. It compares deduplicated text actually present in supported history records, counted once on first exposure. Internal logs, script size, and emitted-but-not-confirmed-visible CLI/MCP responses earn no context credit. Bytes are always available; an optional reference tokenizer enables a clearly labeled estimate:

```sh
owlmatic measure prepare-tokenizer
```

This explicit command downloads a fixed public vocabulary with a pinned SHA-256 hash. Subsequent counting is offline. Reference tokens are **not** Codex/Claude billing tokens. No repeated-context multiplier is assumed. Missing/nontext tool evidence prevents a complete context comparison.

## Capture and architecture

`owlmatic capture init NAME --source-task manual-deploy-1` associates a draft with its original task. Successful fixture validation links that evidence to the resulting immutable draft reference. Schema-only validation does not qualify a baseline. Publishing under a different catalog reference requires an explicit baseline link to that exact published reference.

Capture still scaffolds a workflow; this feature does not automatically synthesize or certify a script from a transcript. It adds the evidence association needed to measure reuse without guessing from the generated code.

The implementation separates immutable contracts and pure estimation (`measurement/`) from local history decoding, reference tokenization, and SQLite (`infrastructure/measurement/`). Application services depend on protocols for those boundaries. CLI/MCP adapters only translate requests and responses. SQLite migrations preserve run and legacy statistics records. The independent dashboard vendors a versioned wire contract and has no dependency on the core package.

## Privacy, export, and limitations

Raw transcripts are read but never copied into the measurement ledger. The ledger stores normalized usage, hashed source identities, selected line ranges, run IDs, attribution, and coverage. CLI/MCP emission records contain counts only. Files are bounded to 32 MiB and events to 1 MiB. Histories are sensitive local input, not instructions to execute. The ledger is an inspectable local accounting record, not tamper-proof provider billing.

V2 export must explicitly enable `include_measurements`. It sends aggregate usage and comparisons; per-workflow model/workload details also require workflow identity sharing. Task IDs, source paths, source hashes, prompts, outputs, and credentials are excluded. Upgrade the receiver before opting into v2. V1 exports remain unchanged and legacy manually configured estimates remain labeled separately.

Measurement cards cover retained measurement history, not the run statistics calendar window. Setup/maintenance costs are charged once per version in that retained scope. Do not sum snapshots or add legacy estimates to measurement estimates. No automatic price conversion, dollar saving, human-time saving, or universal efficiency claim is made. The [matched benchmark protocol](benchmark-method.md) remains necessary to evaluate efficacy against both manual execution and direct scripts.
