# Statistics and savings

`owlmatic dashboard --days 30` writes a private HTML snapshot to `$OWLMATIC_HOME/reports/dashboard.html`. Use `--output /existing/directory/report.html` for another destination. The report uses packaged CSS, has no JavaScript or remote assets, and opens directly in a browser. It contains no run inputs, outputs, logs, workspace paths, or credentials. Workflow references and baseline source descriptions are escaped but still potentially sensitive; review before sharing.

`owlmatic stats --days 30 --json` returns the same typed statistics. Neither command launches workflows or loads per-workflow content into agent context. There is no new MCP tool or always-loaded Skill instruction. CLI adapters only construct requests; a service calculates statistics over a separate repository port. SQLite groups ledger metadata; the writer handles HTML and atomic filesystem output.

## What is measured

- Unique invocations in the retained local ledger, grouped by exact workflow reference and UTC start date. A repeated idempotency key retrieves the same invocation and is counted once.
- Verified outcomes, including a valid negative finding. Execution errors, cancellations, unknown effects, and inconclusive results do not earn savings credit.
- Recorded workflow execution duration. This is machine runtime, not agent task latency or human time saved. Unfinished or lost invocations may have no recorded duration.
- Running invocations, execution errors, and excluded validation/legacy invocations. Statistics do not expire leases or reconcile runs; use `recover` for lifecycle maintenance.

The date window includes today and the preceding `days - 1` UTC calendar days, through the snapshot timestamp, based on invocation start time. Runs predating purpose tracking are unknown and excluded instead of guessed to be real reuse. New `validate` runs are tagged explicitly. Synthetic examples invoked through `run` count as executions; they do not establish real-world efficacy.

Retention deletes history. Reports are labeled **retained local runs**, not lifetime usage. Two data directories are separate scopes. Exported HTML files remain snapshots; regenerating a report reflects current retained records and current baselines.

## Configuring the model

```sh
owlmatic stats baseline '<exact-ref>' \
  --manual-tokens 8000 --owlmatic-tokens 800 --setup-tokens 20000 \
  --source 'Planning estimate, example figures only'

# After matched trials with the same host, model, task, and counting convention:
owlmatic stats baseline '<exact-ref>' \
  --manual-tokens 7900 --owlmatic-tokens 950 --setup-tokens 18000 \
  --basis benchmark --sample-size 10 \
  --source 'Matched trials; model/version and evidence recorded in benchmark report'
```

Figures above are illustrative, not measured results. Baselines must use a consistent total-token definition, including host prompts, discovery, reasoning where reported, retries, failures, log inspection, and fixed Skill/tool overhead. Do not mix input-only counts with total tokens. Include capture, validation, and maintenance in the setup allowance. If hosts/models have materially different costs, use a documented weighted baseline or separate data directories; this dashboard does not attribute costs by host/model.

Baselines are local SQLite records keyed by immutable workflow reference. Updating a baseline replaces its values and recalculates the current snapshot; it does not rewrite past exports. Bundle updates require their own baseline. Source and sample count preserve provenance, but Owlmatic does not verify supplied measurements. `basis=benchmark` still produces estimates when extrapolated to other runs.

For each version with at least one terminal execution:

```text
manual equivalent = verified invocations × manual_tokens
Owlmatic cost      = all terminal invocations × owlmatic_tokens
net tokens saved   = manual equivalent − Owlmatic cost − setup_tokens
```

Every terminal attempt is charged, including execution failures. Running attempts earn no credit and receive no final cost until they finish. Negative results stay negative. Missing baselines mean unknown savings. Coverage is modeled terminal invocations divided by all terminal invocations; unmodeled workflows contribute to usage but not savings totals.

The full setup allowance is deducted once per observed, modeled version **in the selected window**. This is a conservative window scenario, not an amortized ledger. Do not sum overlapping windows or interpret the estimate as audited lifetime savings. Daily charts show operating estimates **before setup**; their sum minus setup equals the net card. The table shows net estimates per version.

## What is not measured yet

Generic MCP does not expose Codex/Claude Code's full conversation usage. Output bytes are not tokens. Provider usage ingestion, cached/input/output/reasoning counts, task-session correlation, and price accounting are not implemented. No dollar or human-time saving is claimed.

To establish actual savings, use the [matched benchmark protocol](benchmark-method.md), including the same scripted workflow invoked without Owlmatic. Compare successful task completion as well as tokens and elapsed time. Future usage ingestion should accept explicit session records with provenance and idempotent IDs, rather than scrape private host history or infer a tokenizer from byte counts.
