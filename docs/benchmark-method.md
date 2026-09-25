# Measuring Owlmatic

The benchmark must distinguish the value of automation from the incremental value of Owlmatic.

## Comparisons

Run each task in a fresh session under both Codex and Claude Code using four configurations:

1. Normal repository exploration.
2. A written procedural Skill.
3. A Skill that directly calls the same executable used by Owlmatic.
4. Owlmatic discovery and execution.

Keep the executable and success criteria identical between configurations 3 and 4. Pin the task fixtures, agent version, model, and settings. Randomize configuration order and repeat trials. Agent evaluations use the caller's model subscription or API account; deterministic fixture tests do not require either agent.

## Measurements

Collect provider-reported input, cached-input, output, and reasoning usage when exposed. Record unavailable fields as unavailable. Tool-output byte counts are useful but are not a substitute for model token or billing data.

Also measure task correctness using an independent verifier, tool calls, elapsed time, failed searches, repairs, and unintended effects. Include the cost of capture and maintenance when estimating amortized savings.

## Required scenarios

- Successful tests, environment setup, OAuth simulation, log collection, regression checking, and remote-operation simulation.
- Broken services, skipped tests, stale reports, missing dependencies, and incompatible environments.
- No matching workflow and misleadingly similar workflow names.
- Changed bundles, expired trust, interrupted runs, and uncertain remote effects.
- Growing catalogs with the same number of exposed MCP tools and Skill instructions.

The target is at least 70% lower median task-token usage than manual reconstruction on repeatable examples, without a correctness regression. Report the comparison against direct scripted Skills separately. These are evaluation targets, not measured results or production-safety guarantees.

Cross-agent reuse is a separate acceptance condition: a draft authored with one agent must be discoverable and executable by the other without changing its contract.

## Run the transport sample

`python -m benchmark.transport` registers the bundled examples in a temporary catalog, trusts only a synthetic workflow, runs it, and measures UTF-8 payload sizes. It makes no agent/model calls and cleans up the temporary directory. One local CPython 3.12/macOS sample measured five tool definitions at 3,463 bytes total, the Skill at 1,450 bytes, discovery at 369 bytes, and the run result at 732 bytes. Timings, paths, logs, SDK versions, and serialization can change these values. Tool definitions are fixed overhead, not a cost incurred once per workflow.

## Explicit cost model

Let `M` be manual task tokens, `R` be search/execution task tokens including fixed integration overhead, `C` be capture/review/test tokens, and `U` be later maintenance tokens. After `N` uses, the modeled reduction is `N*M - (C + N*R + U)`. Break-even requires `M > R` and `N > (C+U)/(M-R)`. Compute this separately against a procedural Skill and a Skill invoking the identical executable.

For illustration only: if reconstruction costs 8,000 tokens, reuse costs 1,200, and capture plus maintenance costs 18,000, three uses cross break-even. If a direct scripted Skill already costs 900 tokens, reuse at 1,200 saves nothing against that baseline. Discovery could still improve correctness or coverage, but those benefits need separate evidence.

Manual costs include repository listings/reads, constructing commands, repeated tool round trips, logs, and interpreting success. Reuse mainly removes repository exploration and intermediate output; executing ordinary code does not consume model tokens. Hidden costs include Skill/tool schemas, failed searches, selecting a similar but unsuitable workflow, describe calls, polling, trust setup, repairs, and capture itself. Prompt-cache billing may differ from raw token counts. Do not double count provider reasoning tokens when already included in output usage.

For live trials, create fresh disposable project directories and host sessions per configuration; install only that configuration's instructions/tools. Record the fixture digest, host/model versions, task prompt, provider usage, independent outcome, and effects. Treat usage fields absent from host output as unavailable. No live Codex or Claude Code model trial has been run by this implementation's default tests. Native adapter setup and real SDK stdio integration are tested separately from model behavior.
