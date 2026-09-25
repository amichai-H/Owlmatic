# Optional statistics export

Owlmatic works without a dashboard server. Local reports, manual exports, and automatic delivery are independent options. Nothing is sent until a user installs an enabled YAML configuration. Workflow manifests cannot enable exports.

## Configure

Copy [the example YAML](../config/observability.example.yaml), select a mode and sharing scope, then install it:

```sh
owlmatic export configure --file observability.yaml
owlmatic export status --json
owlmatic export preview --json
owlmatic export push --json
owlmatic export retry --json
owlmatic export disable --json
```

`configure` validates and copies the YAML into `$OWLMATIC_HOME/observability.yaml`, with private permissions; it does not send data. Subsequent edits belong in that installed file or require another configure command. There is no automatic project-directory discovery of export configuration. Unknown fields and invalid destinations are rejected. See the [configuration schema](../contracts/observability-v1.schema.json).

Modes:

- `disabled` (default): no remote delivery.
- `manual`: explicit `push`, or the same command invoked by an existing scheduler/CI. `retry` retries a pending snapshot without creating a new one.
- `after_workflow`: the trusted caller starts a separate completion observer; that process waits for the run to terminate and performs delivery/retries. The workflow worker never receives the export token through this integration. Validation fixtures do not trigger export. Equal automatic snapshots are coalesced after acknowledgement.

The observer is best effort, not a persistent agent or scheduler. A killed observer, unavailable machine, or exhausted retry limit leaves manual recovery necessary; a later export includes the current ledger. Workflow execution never waits for the network and an export failure cannot change its outcome. Check `export status` and the private `export-diagnostic.json` for delivery/setup failures. After changing credential bindings or destinations, new invocations get the new observer environment; restart old observers or use manual delivery for the transition.

`dashboard.local.enabled: true` updates the optional local HTML report after ordinary workflow completion, independently of export mode. The explicit `owlmatic dashboard` command generates it even when automatic local reporting is disabled.

## Sharing and credentials

Only aggregate usage totals are selected by default. `savings_estimates`, `daily_breakdown`, and `workflow_identifiers` each require opting in. Savings remain estimates; no provider billing is inferred. The [public snapshot contract](../contracts/statistics-snapshot-v1.schema.json) contains no inputs, outputs, logs, credentials, workspace paths, raw run IDs, or baseline source text. A random source ID identifies one data directory; it is pseudonymous, not anonymous. Workflow identifiers, if enabled, can disclose private names.

Remote endpoints require HTTPS. Literal loopback IPs allow HTTP for local testing. URL credentials, query strings, fragments, redirects, and implicit environment proxies are unsupported. Bind the bearer token by environment-variable name; never put its value in YAML. TLS uses the system/Python trust store. VPN routing and endpoint access remain local environment concerns. Socket timeouts do not bound OS DNS resolution time, another reason delivery runs outside execution.

## Delivery semantics

Preview freezes the exact pending snapshot without resolving credentials or contacting the endpoint. Push sends that payload. The private outbox holds one pending snapshot, bounded to at most 1 MiB plus metadata. Newer state is picked up on a subsequent export after acknowledgement. Pending snapshots expire according to configured retention.

Each attempt sends `Idempotency-Key: <snapshot_id>`. Retries preserve the ID and exact body. Transient connection failures and HTTP 408/429/5xx receive bounded exponential backoff in automatic mode; permanent rejections wait for an explicit retry after correction. `push` performs one attempt, allowing external schedulers to manage cadence. A crash after remote acknowledgement can cause a repeat delivery; receivers must deduplicate.

Configuration changes invalidate pending data before a subsequent attempt. Disabling clears pending data but cannot recall an in-flight request or delete data already delivered. Application configuration operations serialize with delivery; editing the YAML externally is picked up on the next operation. A single-slot outbox favors current snapshots over an exhaustive event history.

Snapshots are **not additive events**. Receivers replace the latest snapshot for a source using its monotonic sequence and deduplicate by snapshot ID. Never sum overlapping windows. Retention and baseline changes can legitimately reduce reported totals. Do not clone the Owlmatic data directory across installations: that also clones source identity and sequence state.

## Separate receiver

[owlmatic-dashboard](https://github.com/amichai-H/owlmatic-dashboard) is an independent Python server with its own configuration, database, authentication, tests, CI, and deployment lifecycle. It consumes the public v1 contract and does not import Owlmatic internals. Another service can implement the same contract. New transport implementations can satisfy `SnapshotSender`; the first release implements HTTP only and loads no arbitrary plugins from YAML.

For local testing, start the receiver at `http://127.0.0.1:8765`, give Owlmatic its ingest token, configure `manual`, preview, then push. Open the receiver UI and enter its separate read token. After validating manual delivery, switch the client to `after_workflow` and run a trusted synthetic example.

## Version 2 measurement exports

Deploy a v2-capable dashboard receiver first, then opt in with `version: 2`, `export.schema_version: "2"`, and the `/api/v2/snapshots` endpoint. `include_measurements: true` shares aggregate task usage and savings comparisons; workflow/model/workload details also require `include.workflow_identifiers: true`. Raw histories, prompts, outputs, source paths, task IDs, and credentials are never included. `source_label` is explicitly configured public metadata. V1 payloads and ingestion remain supported without silent downgrade.

The dashboard reads `/api/v2/sources` for labels, measurements, and server receipt timestamps. `/api/v1/sources` retains its original shape, projecting v2 records to legacy run statistics. Refresh fetches the latest received snapshot; it does not trigger local exports. Measurement updates trigger automatic exports only in `after_workflow` mode; manual mode still requires `owlmatic export push`. See [measurement configuration](measurement.md).
