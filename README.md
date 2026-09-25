# Owlmatic

Executable memory for coding agents. Think once. Run automatically.

Owlmatic lets Codex and Claude Code search a workflow catalog, run a versioned executable, and receive compact verified results. Workflows and logs stay outside normal agent context.

Python 3.11+, Linux/macOS. This is an alpha with explicit contracts and tested boundaries. It is not yet a hardened enterprise execution platform.

## Try it

Install from this checkout; no published PyPI release is assumed:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'

export OWLMATIC_HOME="$PWD/.owlmatic"
owlmatic examples --json
owlmatic find 'oauth rotation' --json
```

Search returns a reference such as `examples/example.oauth@<sha256>`, its applicability, and its trust status. Review the bundled example and use that **exact returned reference**:

```sh
owlmatic describe '<ref>' --json
owlmatic trust '<ref>' --json
owlmatic run '<ref>' --input '{"healthy":true}' --workspace . --json
```

The synthetic OAuth example verifies rotation, persistence, old-token rejection, and reuse of the stored token. Passing `{"healthy":false}` deliberately breaks persistence. It never accesses Workday or real credentials.

An abbreviated successful response:

```json
{"run":{"run_id":"run_...","state":"completed","outcome":"pass","effects":"completed","outputs":{"token_rotated":true,"token_persisted":true,"old_token_rejected":true,"stored_token_usable":true}},"outputs_omitted":false}
```

The actual response also includes the workflow reference, profile, target, timings, check counts, and `finalization_pending`. A failed check is `state=completed,outcome=fail`; execution errors are separate. CLI exit codes are 0 for a successful command, 1 for failed verification/fixture validation, and 2 for rejected or erroneous execution. Exit zero on a still-running job does not establish success.

## Codex and Claude Code

Both use the same CLI, application services, workflow contracts, and MCP implementation. There is no host-specific execution engine.

```sh
owlmatic setup codex --project /path/to/project
owlmatic setup claude --project /path/to/project
```

Setup previews the project Skill path and exact MCP registration command. Add `--apply` to copy the Skill; then run the returned registration command from your project directory. Existing conflicting files are preserved. Codex uses `.agents/skills/owlmatic`; Claude Code uses `.claude/skills/owlmatic`.

The MCP server is `owlmatic mcp serve`, using stdio. Ensure both hosts receive the same `OWLMATIC_HOME` if you override its default, `~/.owlmatic`. Five tools are exposed regardless of catalog size:

| Tool | Contract |
| --- | --- |
| `owlmatic_find` | Query plus optional environment/repository/profile; at most three candidates |
| `owlmatic_describe` | Exact reference; selected manifest and input/output schemas |
| `owlmatic_run` | Exact reference, explicit workspace, inputs, profile, environment, optional request ID |
| `owlmatic_inspect` | Run ID; status, failures, or a bounded artifact page |
| `owlmatic_cancel` | Run ID; cancellation request, without a rollback promise |

Each MCP response contains one compact JSON text document. It avoids duplicated text/structured content and large advertised output schemas. Typed contracts live in the package and [contract documentation](docs/workflow-contract.md).

## Catalogs and trust

```sh
owlmatic catalog add team /path/to/workflows --json
owlmatic catalog add shared https://example.org/team/workflows.git --kind git --revision '<commit>' --json
owlmatic catalog sync shared --json
owlmatic revoke '<ref>' --json
```

Git stores reviewed code and manifests. Local SQLite FTS5 indexes metadata; discovery never executes code. Bundle hashes include file content and executability. Updates produce a new reference and require a new trust grant. Previously selected bundles remain available. Pin a commit for stable Git catalogs; omitted revisions follow the remote's `HEAD` when explicitly synced.

Profiles choose allowed environments and explicitly inherited environment variables or credential bindings:

```sh
owlmatic profile create integration --environment integration --credential oauth=WORKDAY_TEST_SECRET --json
owlmatic trust '<ref>' --profile integration --json
```

`WORKDAY_TEST_SECRET` names an existing environment variable; its value never belongs in the manifest. A workflow declaring credential `oauth` receives `OWLMATIC_CREDENTIAL_OAUTH`. Credentials, VPN reachability, cloud authorization, and target source trees remain local concerns. See [security boundaries](docs/security.md) before using privileged workflows.

## Execute and inspect

```sh
owlmatic run '<ref>' --input-file inputs.json --workspace /path/to/source --request-id task-123 --wait 0 --json
owlmatic inspect '<run_id>' --wait 20 --json
owlmatic inspect '<run_id>' --view failures --json
owlmatic inspect '<run_id>' --view logs --max-bytes 4096 --json
owlmatic cancel '<run_id>' --json
owlmatic recover --json
owlmatic inspect '<run_id>' --view diagnostic --json
owlmatic cleanup --days 7 --json
```

Long jobs continue in a separate local worker, with an independent guardian for process-group cleanup and deadlines. Logs stay in private run artifacts and are fetched on demand. Follow `next_cursor` to page through them. Reusing a request ID with identical inputs retrieves the existing invocation; changed inputs are rejected. A declared concurrency input locks the corresponding resource locally. Partial/unknown effects retain that lock until the operator verifies external state and calls `owlmatic reconcile '<run_id>' --release-lock`.

## Savings dashboard

The dashboard is optional. Use the local HTML viewer below, or send selected metrics to the independently deployed [Owlmatic Dashboard](https://github.com/amichai-H/owlmatic-dashboard). Remote delivery is disabled by default and configured through YAML, with manual and after-workflow modes. Start with the [two-repository local quickstart](docs/try-it.md), or read [configuration, privacy, and delivery](docs/observability.md).

```sh
owlmatic dashboard --days 30
owlmatic stats --days 30 --json
```

Open the returned HTML path in a browser. This offline snapshot shows daily trends, per-version workflow counts, verified completion, runtime, and baseline coverage. It requires no server, remote assets, or additional dependencies. Regenerate it to refresh the data.

Token savings remain unknown until you configure a baseline for an exact workflow reference:

```sh
# Illustrative assumptions; replace these with your own matched measurements.
owlmatic stats baseline '<ref>' --manual-tokens 8000 --owlmatic-tokens 800 \
  --setup-tokens 20000 --source 'Planning estimate; not measured'
owlmatic dashboard --days 30
```

Savings are explicitly **estimated**: verified tasks earn manual-equivalent credit; all terminal attempts incur the configured Owlmatic cost; setup is deducted. Validation and legacy runs with unknown purpose are excluded. Provider tokens, monetary savings, and human time saved are not inferred from runtime or output bytes. See [statistics and measurement](docs/statistics.md).

## Capture a solved procedure

```sh
owlmatic capture init team.health --output ./workflows/team-health --json
# Replace the placeholder procedure, checks, schemas, and fixtures.
owlmatic validate ./workflows/team-health --json
```

Capture is assisted authoring: the current agent edits a scaffold and independent healthy/broken fixtures. It does not analyze shell history, infer determinism, call another model, publish, or grant trust. `validate` executes fixtures in disposable workspaces; `--schema-only` checks structure without executing. Wrap existing task runners and CI scripts instead of rewriting them.

The six [examples](examples/README.md) cover E2E, bootstrap, OAuth, log collection, regression checks, and a runner simulation.

For a real-time local deployment exercise, use the [deploy-and-monitor lab](scenarios/deploy_monitor/README.md).
It starts separate mock deployment and log APIs, verifies a one-minute window, and tests late errors and missing
evidence. Its [measured Luna benchmark](docs/deploy-monitor-benchmark.md) compares manual work, direct automation,
and Owlmatic—including failures and a follow-up improvement to the agent guidance.

## Development and evaluation

```sh
python -m ruff check src tests examples benchmark scenarios
python -m ruff format --check src tests examples benchmark scenarios
python -m mypy
python -m pytest -q
python -m build
python -m benchmark.transport
python -m scenarios.deploy_monitor.live_check
```

`benchmark.transport` measures payload bytes without invoking a model. The opt-in deployment lab additionally
records Codex-reported tokens; neither smaller payloads nor a single trial establishes general savings. See
[benchmark methodology](docs/benchmark-method.md), [architecture and roadmap](docs/architecture.md), and [contributing](CONTRIBUTING.md).

See [recovery and upgrade instructions](docs/recovery.md) before upgrading an existing data directory.

Apache-2.0. No hosted service, telemetry, semantic embedding service, or private company access is required.
