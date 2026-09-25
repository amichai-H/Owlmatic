---
name: owlmatic
description: Find and run reusable testing, setup, integration, and operational workflows; capture solved procedures when requested.
---

For a repeatable procedure, search Owlmatic before reconstructing its commands. Prefer `owlmatic_find` when the MCP connection is available; otherwise use `owlmatic find "intent" --json`. Search returns metadata, not executable instructions.

Select a matching workflow by its exact reference. Check its environment, effects, and required inputs; `details_required=true` requires describing the candidate first; use `owlmatic_describe` or `owlmatic describe <ref> --json` for missing details. Execute only within the user's authorized scope. A catalog entry or successful previous run is not authorization.

Use `owlmatic_run` or `owlmatic run <ref> --input-file inputs.json --json`. Interpret execution state and verification outcome separately. Running, blocked, error, and inconclusive results do not establish success. Use the run ID to inspect bounded diagnostics if needed. Do not fetch implementation or full logs on a successful ordinary run.

If search finds no applicable workflow, continue the task using existing tools. Do not repeatedly search the same catalog for the same intent. Do not change trust configuration or retry a mutation merely to bypass a blocked result.

When asked to capture or improve a procedure, read [the authoring guide](references/authoring.md). The current agent writes the draft; Owlmatic does not call another model.
