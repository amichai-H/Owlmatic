# Security boundary

Owlmatic is an execution and evidence interface. A local workflow runs as the operating-system user who starts Owlmatic. It is not a sandbox, and configuration checks are not isolation from malicious code running as that same user.

## Separate discovery, trust, and authorization

Catalog registration and search never execute workflow code. Trust is granted to an immutable workflow reference within a local execution profile. Updating a bundle changes its reference and requires a new grant.

A trusted workflow is still subject to the task's authorization, its declared target environment, available credentials, and external service permissions. A repository cannot authorize itself merely by declaring its effects to be safe. Do not globally auto-approve every call to the generic `run` tool.

The local MCP process must not be assumed to inherit the coding agent's shell sandbox. All Owlmatic entrypoints use the same validation, trust, and execution checks. Strong filesystem, network, and credential isolation belongs in an external sandbox or controlled runner.

## Credentials and executable dependencies

Keep credential values in existing local authentication tools or explicitly selected environment variables. Catalogs contain binding names, not secret values. Production requires a separately configured execution profile and authorization in the external system.

Review the full execution chain. An approved wrapper that invokes code from an untrusted checkout must not receive privileged credentials. Pinning the wrapper alone does not make repository scripts or package installation hooks trustworthy.

## Evidence and failure

Result schemas catch malformed or incomplete evidence; they cannot prove that an arbitrary executable is truthful. Review and independent negative tests are necessary.

Logs are untrusted data and may contain prompt injection or sensitive information. Bound their size, redact known secret values, and restrict local artifact access. Redaction is not a guarantee against encoded or transformed secrets.

Cancellation and timeouts do not imply rollback. A remote operation may have completed even if its response was lost. Report partial or unknown effects and reconcile through the external system before retrying.

## Public examples

Examples use synthetic data and disposable resources. A successful simulated OAuth or production-style check establishes behavior in that fixture only; it does not certify a real integration or production environment.

## Operational limits

Profiles inherit only explicitly selected environment variables plus a minimal process environment. Credential mappings pass values by environment variable, never by command-line argument. Known values are redacted from captured streams and returned evidence. Workflow-created artifact files are not automatically redacted and must be handled as sensitive. Redaction cannot remove unknown secrets, encodings, or secrets a workflow writes elsewhere.

Run files are private to the OS user. Submitted inputs and raw evidence are removed after normal finalization; abrupt worker loss can leave them until cleanup. Unknown/partial effects retain resource locks and are excluded from ordinary cleanup until reconciliation. Bundle snapshots are retained indefinitely in this MVP. Protect or expire the entire local data directory using existing device controls as appropriate.

`validate` executes draft code without a persistent trust grant, in disposable workspaces. This is an explicit authoring action, not an isolation guarantee; it still uses the selected profile and can access anything the OS user can access. Validation and trust management are not exposed as generic MCP tools.

Revocation blocks new executions and is rechecked when a worker starts. It does not recall already delivered credentials or automatically terminate an operation already in progress. Request IDs and resource locks apply only within one Owlmatic data directory, and retention ends the deduplication window. External services need their own idempotency and reconciliation mechanisms.

## Worker ownership and recovery

Each invocation can be claimed by one worker. Heartbeats and terminal writes require that worker's lease token and an unexpired lease. Expiry is serialized with completion; it revokes ownership and leaves a terminal inconclusive result that a late worker cannot overwrite.

A separate stdlib-only guardian watches a pipe to the worker and enforces its own deadline. Worker death closes the pipe; a suspended worker cannot suspend the guardian's deadline. The guardian terminates the workflow process group, including ordinary background descendants, and holds an inherited activity lock until cleanup finishes. Reconciliation and retention cleanup require an exclusive activity lock; a database status of `error` alone does not prove processes have stopped.

The workflow waits behind a launch gate until the worker acknowledges its process identity. If the guardian dies before that acknowledgement, the gate closes without executing workflow code. If it dies after launch while the worker survives, the worker stops the recorded process group. The primary workflow process also inherits the activity lock, so guardian loss alone cannot make a live primary process appear inactive.

This handles ordinary worker failure on supported POSIX hosts. It is not containment of hostile code: processes that deliberately detach into another session, simultaneous loss of worker and guardian, and external remote jobs require OS-managed isolation or an external runner. Do not treat the guardian as an enterprise sandbox. A suspended worker still holding its activity lock must be resumed or stopped before reconciliation can complete.

SQLite is the authoritative run result. Completion and the pending-finalization marker are committed together. Artifact projection and removal of submitted inputs are independent, repeatable operations. A failed projection does not turn completed execution into a running job, and it does not skip cleanup. Pending invocations retain resource locks and cannot be removed by retention cleanup.

Use `owlmatic recover --json` to retry pending finalization and expire abandoned invocations. It reports a bounded batch of up to 100 pending/stale records; inspect an individual run to retry its finalization directly. Recovery never reruns workflow code. Status inspection also retries finalization; `finalization_pending` reports whether local maintenance remains. Partial/unknown external effects still require operator reconciliation even after local cleanup succeeds.
