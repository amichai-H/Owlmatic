"""Copy this script outside the checkout and run with a fresh venv's python -I."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

import owlmatic
from owlmatic.bootstrap import create_application
from owlmatic.domain import RunRequest, RunSummary, SearchRequest
from owlmatic.infrastructure.authoring import resource
from owlmatic.statistics import SavingsBaseline, StatisticsRequest


async def smoke() -> None:
    module = Path(owlmatic.__file__).resolve()
    assert module.is_relative_to(Path(sys.prefix).resolve()), (
        f"Imported outside isolated environment: {module}"
    )
    assert "site-packages" in module.parts and "src" not in module.parts
    with tempfile.TemporaryDirectory(prefix="owlmatic-installed-") as directory:
        root = Path(directory)
        app = create_application(root / "home")
        assert app.examples().workflows == 6
        ref = app.catalog.find(SearchRequest(query="oauth rotation")).results[0].ref
        app.catalog.trust(ref)
        result = app.execution.run(RunRequest(ref=ref, workspace=root, inputs={"healthy": True}))
        assert result.run.outcome == "pass", result.model_dump_json()
        draft = app.authoring.capture("installed.health")
        assert app.authoring.validate(draft.draft).valid
        assert (resource("skill") / "SKILL.md").is_file()
        app.statistics.baseline(
            SavingsBaseline(ref=ref, manual_tokens=1000, owlmatic_tokens=100, source="wheel fixture")
        )
        stats = app.statistics.report(StatisticsRequest())
        assert stats.runs == 1 and stats.excluded_validation_runs == 2
        assert stats.estimated_net_tokens_saved == 900
        dashboard = app.dashboard.generate(StatisticsRequest())
        assert "Less rediscovery" in dashboard.path.read_text()
        assert app.exports.push().status == "disabled"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-I", "-m", "owlmatic", "mcp", "serve"],
            env={**os.environ, "OWLMATIC_HOME": str(root / "home")},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 5
                response = await session.call_tool(
                    "owlmatic_run", {"ref": ref, "workspace": str(root), "inputs": {"healthy": True}}
                )
                assert not response.isError
                assert isinstance(response.content[0], TextContent)
                assert RunSummary.model_validate_json(response.content[0].text).run.outcome == "pass"
        print(
            json.dumps(
                {"installed_wheel": "pass", "cli_core": "pass", "mcp_stdio": "pass", "fixtures": "pass"}
            )
        )


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(smoke(), timeout=60))
