# Recovery and upgrades

SQLite owns the authoritative run ledger; JSON results are projections. Never delete `catalog.sqlite` as a routine fix: it contains idempotency records and resource locks as well as rebuildable discovery metadata.

## Failed worker or storage operation

1. Inspect the run. `WORKER_LOST` means its heartbeat expired and the old worker is fenced from committing a result. It does not establish rollback.
2. Inspect `--view diagnostic` for a safe startup failure code; use `--view logs` for bounded workflow output when relevant. Corrupt databases, contention, missing paths, permissions, and full disks have distinct public error codes.
3. Restore storage availability. `owlmatic recover --json` retries artifact projection and input removal. Pending finalization is durable and idempotent. It does not rerun a workflow.
4. If recovery reports an active local execution, let its guardian stop it. A suspended worker must be resumed or terminated. Do not remove guard files or resource locks to bypass the activity check.
5. For partial/unknown effects, independently verify the external system before calling `owlmatic reconcile <run_id> --release-lock`. Reconciliation refuses live processes and unfinished local cleanup.

A storage failure before terminal commit leaves the invocation inconclusive after lease expiry. A failure after that commit leaves the true result intact with finalization pending. An unavailable filesystem can prevent even diagnostics from being written; no implementation can promise durable diagnostics when all available storage is failing.

## Database version 1 to 2

Stop old Owlmatic MCP servers and workers before upgrading. Mixed-version workers are unsupported: legacy code does not implement the lease protocol. Back up the complete data directory while stopped, including SQLite WAL state if present.

Opening with the new version performs a transactional version-2 migration. Historical results and resource locks are preserved. Legacy active invocations become `UPGRADE_INTERRUPTED` with unknown effects; they are never silently adopted or restarted. Terminal records are marked for artifact repair and cleanup. Run `owlmatic recover --json`, then reconcile interrupted external operations.

Unknown database versions are rejected rather than overwritten. Rollback requires restoring the complete pre-upgrade backup and the matching application version; do not run the old binary against the new schema. For a corrupt database, preserve the damaged directory for diagnosis and restore a consistent backup rather than recreating an empty execution ledger.

Guard files are stable coordination inodes and are deliberately retained. Bundle garbage collection and guard-file garbage collection are not part of ordinary run retention.

## Database version 2 to 3

The statistics release adds a transactional `savings_baselines` table and advances the schema to version 3. Existing run, lease, and lock records remain intact. New runs record workflow versus validation purpose; existing records default to unknown and receive no savings credit. A version-1 database first receives the lifecycle migration described above, then the statistics table, within the same transaction.

Stop old processes and back up before upgrading. Mixed versions are unsupported; a version-2 binary will reject the version-3 database. Restore the full backup to roll back. See [statistics](statistics.md) for baseline and retention semantics.
