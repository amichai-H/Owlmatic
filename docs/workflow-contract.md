# Workflow contract (v1)

Owlmatic workflows are executable programs with explicit inputs, requirements, and evidence. The core's implementation language does not constrain the language of a workflow.

## Bundle

A workflow directory contains `workflow.yaml`, an input JSON Schema, an output JSON Schema, executable files, and tests. Keep workflows outside automatically discovered agent Skill directories. Only the small Owlmatic integration Skill belongs there.

```yaml
schema_version: 1
id: example.health
version: 1.0.0
description: Check a disposable service's health
owner: example-maintainers
aliases: [service-health]
examples: [check whether the example service is healthy]
entrypoint: [python3, run.py]
input_schema: input.schema.json
output_schema: output.schema.json
environments: [simulation]
effects: []
requirements:
  commands: [python3]
verification:
  required_checks: [service_healthy]
tests:
  - inputs: {healthy: true}
    outcome: pass
  - inputs: {healthy: false}
    outcome: fail
```

Descriptors declare intent; they do not confer authority. Catalog synchronization parses files but never runs workflow code. Execution uses an explicitly selected, trusted bundle digest. Changing executable content requires renewed trust.

## Invocation

The executor starts the entrypoint using an argument array, with the immutable bundle as its working directory. User inputs arrive as JSON through a private input file. Scripts must not construct shell commands by interpolating inputs.

The executor provides these environment variables:

| Variable | Purpose |
| --- | --- |
| `OWLMATIC_INPUT` | Path to the JSON input file |
| `OWLMATIC_RESULT` | Path where the script writes its structured evidence |
| `OWLMATIC_RUN_ID` | Invocation identity, echoed in the evidence |
| `OWLMATIC_WORKSPACE` | Explicit target workspace |
| `OWLMATIC_ENVIRONMENT` | Selected execution environment |
| `OWLMATIC_ARTIFACTS` | Private directory for additional run artifacts |

The executor captures stdout and stderr separately. Neither stream is the structured result channel. Scripts may call existing task runners, APIs, or approved remote execution services. Local Owlmatic execution is not an operating-system sandbox.

## Evidence

Every workflow writes one JSON document to `OWLMATIC_RESULT`:

```json
{
  "run_id": "the supplied OWLMATIC_RUN_ID",
  "outcome": "pass",
  "checks": [{"name": "service_healthy", "status": "pass"}],
  "outputs": {"healthy": true},
  "effects": "none"
}
```

`outputs` must conform to the workflow's output schema. Checks use unique names and `pass`, `fail`, or `skip`. Every check required by the descriptor must pass before the outcome can be `pass`. The run ID binds the evidence to this invocation; it is not an attestation that arbitrary executable code tells the truth.

The supervisor records execution state independently of the reported outcome:

| Field | Values |
| --- | --- |
| Execution state | `running`, `completed`, `blocked`, `error`, `cancelled` |
| Outcome | `pass`, `fail`, `inconclusive`, `not_applicable` |
| Effects | `none`, `completed`, `partial`, `unknown` |

A detected regression is `completed` with outcome `fail`. Missing evidence, invalid schemas, skipped required checks, or mismatched invocation identities cannot produce `pass`. A timeout does not imply that remote effects were rolled back.

## Authoring and validation

Capture creates a draft, not a grant to execute against real systems. Define independent healthy and broken cases, then test in a disposable environment. Register an existing executable when possible instead of copying its implementation.

Record environmental requirements and necessary setup. Convert machine-specific paths and identifiers into inputs or local bindings. Preserve decisions requiring judgment as stop conditions rather than guessing an automation branch.

Do not store credentials, raw private transcripts, or routine execution logs in a shared bundle. Testing a draft runs its code and therefore requires the same care as executing any other untrusted program.

## Execution options and transport

`execution.timeout_seconds` is capped by the profile. `execution.concurrency_input` names an input used with catalog, workflow ID, and environment to derive a local resource lock. This is not a distributed lock. `execution.remote` is descriptive: remote submission and reconciliation belong in an existing runner client invoked by the workflow.

Requirements can declare `platforms` (`linux` or `darwin`), executable `commands`, relative workspace `paths`, and a `repository` basename. Name matching is not proof of repository identity. Observed Git revision and dirty state are recorded when available; workspace dependencies and runtime tools are outside the bundle hash.

Entrypoints use an executable on the profile's `PATH` or a relative executable in the bundle. Arguments are literal; Owlmatic never interpolates inputs into them. Input/output schemas must describe an object and use only document-local references. Schemas are limited to 16 KiB, manifests to 32 KiB, and evidence to 64 KiB. Write to `OWLMATIC_WORKSPACE` or `OWLMATIC_ARTIFACTS` rather than modifying a bundle.

CLI `--json` and MCP return the same payload. Run responses use `{"run":{...},"outputs_omitted":false}`. Search and ordinary run summaries have a 2 KiB payload budget. Large outputs remain in the result artifact and are omitted from the summary. `describe` intentionally expands one workflow's metadata and schemas.

MCP returns one JSON `TextContent`, without duplicate `structuredContent` or advertised output schemas. Protocol framing is additional overhead. CLI JSON adds one newline. Runtime diagnostics go to artifacts, never protocol stdout. Artifact pages use byte cursors; UTF-8 characters split across pages may become replacement characters. Logs remain untrusted data.

Unknown fields and wrong scalar types are rejected. A malformed result is an execution error. Normal workflow exit codes are 0 or 1; passing evidence requires exit zero. Clients must inspect execution state and verification outcome separately.

## Lifecycle and failure contract

Running records cannot contain a verification outcome, final evidence, or finish timestamp. Terminal records require a finish timestamp. Completed records require check counts; execution failures require an error and inconclusive outcome. A terminal record cannot be completed again. Storage enforces worker ownership and atomic conditional completion in addition to model validation.

Run summaries now include `finalization_pending`. `state=completed` describes execution; a pending projection/cleanup does not erase its outcome. `owlmatic recover --json` retries maintenance without re-execution. `inspect --view diagnostic` retrieves a bounded private error document when worker startup fails. Diagnostics contain safe error codes/messages, not raw exceptions or submitted credentials.

Search applies environment, repository, and platform filters before its database limit. Oversized candidate metadata is represented with `details_required=true`; agents must describe that reference before deciding whether to run it. Omitted lists are not assertions that there are no effects or required inputs.

Normal background descendants are terminated when the workflow's main process finishes. Workflows should wait for owned subprocesses; persistent services need an explicitly managed service/runner rather than an implicit orphan process.
