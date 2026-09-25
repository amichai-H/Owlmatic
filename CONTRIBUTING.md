# Contributing

Use Python 3.11 or newer on Linux or macOS. All fixtures are synthetic; no private repositories, agent subscriptions, or cloud credentials are needed.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m ruff check src tests examples benchmark
python -m ruff format --check src tests examples benchmark
python -m mypy
python -m pytest -q
python -m build
```

## Engineering contract

- Model domain decisions and public requests/results explicitly. Validate at boundaries. Do not use `Any`, unchecked Pydantic model updates, or arbitrary dictionaries to represent domain entities.
- JSON Schema and workflow payloads are intentionally dynamic; use `JsonObject`/`JsonValue` there. Named profile and credential maps have concrete value types.
- Application services depend on ports. External I/O belongs in infrastructure; only the composition root chooses implementations. CLI and MCP adapters parse, dispatch, and serialize.
- Inject clocks, process execution, repositories, and host access. Keep policy tests independent of timing and external services; use disposable paths for infrastructure tests.
- Add negative evidence and interruption cases for changes to execution. Do not equate process exit zero with a successful verification.
- Preserve compact discovery and result budgets. Never add one MCP tool or automatically loaded Skill per workflow.
- Keep the public contract and both adapters aligned. Include docs and migration implications in changes to stored records.

Tests enforce import direction, prohibit unchecked contract updates, and exercise real stdio MCP and subprocess boundaries, worker SIGKILL/SIGSTOP, stale leases, persistence failures, and schema migration. CI runs the quality checks across Linux/macOS and Python versions, then builds and smoke-tests the wheel in a clean virtual environment outside the checkout, asserting the import path and exercising both execution and MCP. It uses the official [checkout](https://github.com/actions/checkout) and [setup-python](https://github.com/actions/setup-python) actions; hosted CI has not run until this project is pushed to GitHub.

For reproducibility, record the Python version and `python -m pip freeze` with benchmark runs. `requirements-dev.lock` records the tested CPython 3.12/macOS environment; install it with `python -m pip install -r requirements-dev.lock -e .`. Compatibility ranges live in `pyproject.toml`, and CI tests resolution across the declared platform/version matrix. The snapshot is not a universal lock or a substitute for artifact hashes and release verification. Dependabot updates are configured.

## Workflow contributions

Prefer wrapping an existing task runner, test suite, or runbook executable. Supply an owner, meaningful search terms, explicit effects, bounded execution, and independent positive/negative fixtures. Test with `owlmatic validate <directory>`. Review the full dependency chain and check for duplicates before publishing through your existing Git review process. Validation neither publishes nor grants trust.

Keep employer code, credentials, proprietary procedures, and execution transcripts out of this public project.
