# Test the two repositories locally

These commands use synthetic OAuth fixtures, a local receiver, and a fresh client data directory. No company repository, production credentials, or cloud account is needed. Python 3.11+ on macOS/Linux is required.

## Terminal 1: receiver

```sh
git clone https://github.com/amichai-H/owlmatic-dashboard.git
cd owlmatic-dashboard
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp dashboard.example.yaml dashboard.yaml
export OWLMATIC_DASHBOARD_INGEST_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export OWLMATIC_DASHBOARD_READ_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
owlmatic-dashboard --config dashboard.yaml
```

Keep this terminal running. Copy the ingest token you generated to Terminal 2 through your normal secret handling method. Use the separate read token to connect at **http://127.0.0.1:8765**. The server does not print credentials. These variables belong to this shell; set them again when starting a fresh shell.

## Terminal 2: client

```sh
git clone https://github.com/amichai-H/Owlmatic.git
cd Owlmatic
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
export OWLMATIC_HOME="$(mktemp -d)"
export OWLMATIC_DASHBOARD_INGEST_TOKEN='<same ingest token from Terminal 1>'
cp config/observability.example.yaml observability.yaml
```

Edit `observability.yaml`: set `export.mode: manual`. For this synthetic demonstration, you can also enable `savings_estimates`, `daily_breakdown`, and `workflow_identifiers`. The example endpoint is already the local receiver.

```sh
owlmatic export configure --file observability.yaml
owlmatic examples --json
REF="$(owlmatic find 'oauth rotation' --json | python -c 'import json,sys; print(json.load(sys.stdin)["results"][0]["ref"])')"
owlmatic trust "$REF" --json
owlmatic stats baseline "$REF" --manual-tokens 1000 --owlmatic-tokens 100 \
  --source 'Synthetic demo assumptions, not measured savings'
owlmatic run "$REF" --input '{"healthy":true}' --json
owlmatic export preview --json
owlmatic export push --json
```

Refresh the dashboard. Expect one workflow execution and one verified task. If savings sharing is enabled, the modeled net saving is **900 tokens**, from the explicit illustrative baseline above. This does not establish real agent savings.

## Automatic mode

Change the YAML to `export.mode: after_workflow`, then:

```sh
owlmatic export configure --file observability.yaml
owlmatic run "$REF" --input '{"healthy":true}' --json
owlmatic export status --json
```

Refresh the dashboard after a few seconds. Expect two executions without running `export push`. The model should show 1,800 estimated tokens if the same sharing scope remains enabled.

## Turn it off

```sh
owlmatic export disable --json
```

Future invocations send nothing. Already received snapshots remain on the receiver. Stop Terminal 1 with Ctrl-C when finished. Your client data directory is the temporary path in `OWLMATIC_HOME`; the server data is local `.data/` beside its YAML.

See [delivery and privacy](observability.md), [measurement limits](statistics.md), and the receiver README before using real organizational data or deploying remotely.
