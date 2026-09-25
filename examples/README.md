# Synthetic workflows

Register all six with `owlmatic examples`. Every example accepts `{"healthy":true}` or `{"healthy":false}` and declares both as fixture tests. Validate individually with `owlmatic validate examples/oauth --json` (or another directory).

| Directory | Checks | Effects |
| --- | --- | --- |
| `e2e` | Real subprocess unittest runner; broken service response | None |
| `bootstrap` | Create and independently read a SQLite schema version | Replaces the contents of `owlmatic-example.sqlite` in the selected workspace |
| `oauth` | Synthetic refresh rotation, persistence, old-token rejection and reuse | Writes a synthetic token artifact |
| `logs` | Three synthetic sources and aggregate error count | Writes run artifacts |
| `regression` | Compare synthetic baseline and release error counts | None |
| `runner` | Subprocess job completion, identity and observed artifact | Writes simulated runner artifacts |

These examples demonstrate contracts and failure detection; they do not validate a real E2E environment, Workday integration, production regression, or remote CI service. Use a disposable workspace, particularly for bootstrap. No credentials or network calls are needed. JSON syntax in `workflow.yaml` is valid YAML and preserves types unambiguously.
