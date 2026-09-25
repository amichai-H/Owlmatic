import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from owlmatic.bootstrap import create_application
from owlmatic.domain import Catalog, RunRequest, RunSummary, SearchRequest, SearchResult
from owlmatic.errors import OwlError
from owlmatic.infrastructure.authoring import FileIntegrations
from owlmatic.serialization import encode
from tests.conftest import ROOT


@pytest.mark.parametrize("name", ["e2e", "bootstrap", "oauth", "logs", "regression", "runner"])
def test_examples_detect_healthy_and_broken_fixtures(tmp_path: Path, name: str) -> None:
    app = create_application(tmp_path / "home")
    report = app.authoring.validate(ROOT / "examples" / name)
    assert report.valid, report.model_dump_json()
    assert [test.actual for test in report.tests] == ["pass", "fail"]
    assert report.trusted is False
    with pytest.raises(OwlError) as caught:
        app.execution.run(RunRequest(ref=report.ref, workspace=tmp_path, inputs={"healthy": True}))
    assert caught.value.code == "UNTRUSTED"
    assert app.catalog.find(SearchRequest(query=f"example.{name}")).results == ()


def test_capture_validate_register_trust_run_and_sync(tmp_path: Path) -> None:
    app = create_application(tmp_path / "home")
    draft = app.authoring.capture("fixture.health")
    report = app.authoring.validate(draft.draft)
    assert report.valid
    app.catalog.add(Catalog(name="team", source=str(draft.draft)))
    found = app.catalog.find(SearchRequest(query="fixture.health"))
    candidate = found.results[0]
    assert candidate.trust == "untrusted"
    app.catalog.trust(candidate.ref)
    run = app.execution.run(
        RunRequest(ref=candidate.ref, workspace=tmp_path, inputs={"healthy": True}, request_id="retry")
    )
    assert run.run.outcome == "pass"
    assert run.run.purpose == "workflow"
    assert len(encode(run).encode()) <= 2048
    retry = app.execution.run(
        RunRequest(ref=candidate.ref, workspace=tmp_path, inputs={"healthy": True}, request_id="retry")
    )
    assert retry.run.run_id == run.run.run_id
    assert not (tmp_path / "home/runs" / run.run.run_id / "inputs.json").exists()
    with (draft.draft / "run.py").open("a") as stream:
        stream.write("\n# Changed bundle requires renewed trust.\n")
    app.catalog.sync("team")
    updated = app.catalog.find(SearchRequest(query="fixture.health")).results[0]
    assert updated.ref != candidate.ref and updated.trust == "untrusted"
    assert app.catalog.describe(candidate.ref).ref == candidate.ref


def test_cli_stdout_is_single_json_document(tmp_path: Path) -> None:
    environment = {**os.environ, "OWLMATIC_HOME": str(tmp_path / "home")}
    result = subprocess.run(
        [sys.executable, "-m", "owlmatic", "examples", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["workflows"] == 6
    found = subprocess.run(
        [sys.executable, "-m", "owlmatic", "find", "oauth rotation", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert found.returncode == 0
    assert SearchResult.model_validate_json(found.stdout).results[0].ref.startswith("examples/example.oauth@")
    assert len(found.stdout.encode()) <= 2049


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_host_setup_is_previewable_and_conflict_safe(tmp_path: Path, host: str) -> None:
    from typing import Literal, cast

    installer = FileIntegrations()
    selected = cast(Literal["codex", "claude"], host)
    preview = installer.setup(selected, tmp_path, False)
    assert not preview.skill.exists()
    result = installer.setup(selected, tmp_path, True)
    assert (result.skill / "SKILL.md").is_file()
    assert result.mcp_command[0] == host
    (result.skill / "SKILL.md").write_text("user edits")
    with pytest.raises(OwlError) as caught:
        installer.setup(selected, tmp_path, True)
    assert caught.value.code == "SETUP_CONFLICT"
    assert (result.skill / "SKILL.md").read_text() == "user edits"


def test_real_mcp_stdio_discovery_execution_and_inspection(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = create_application(home)
    app.catalog.add(Catalog(name="examples", source=str(ROOT / "examples")))
    candidate = app.catalog.find(SearchRequest(query="example.oauth")).results[0]
    app.catalog.trust(candidate.ref)

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "owlmatic", "mcp", "serve"],
            env={**os.environ, "OWLMATIC_HOME": str(home)},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "owlmatic_find",
                    "owlmatic_describe",
                    "owlmatic_run",
                    "owlmatic_inspect",
                    "owlmatic_cancel",
                }
                found = await session.call_tool("owlmatic_find", {"query": "oauth rotation"})
                assert not found.isError
                assert len(found.content) == 1 and isinstance(found.content[0], TextContent)
                assert found.structuredContent is None
                search = SearchResult.model_validate_json(found.content[0].text)
                assert search.results[0].ref == candidate.ref
                executed = await session.call_tool(
                    "owlmatic_run",
                    {"ref": candidate.ref, "workspace": str(tmp_path), "inputs": {"healthy": True}},
                )
                assert not executed.isError, executed.content
                assert len(executed.content) == 1 and isinstance(executed.content[0], TextContent)
                result = RunSummary.model_validate_json(executed.content[0].text)
                assert result.run.outcome == "pass"
                inspected = await session.call_tool("owlmatic_inspect", {"run_id": result.run.run_id})
                assert not inspected.isError

    asyncio.run(asyncio.wait_for(exercise(), timeout=45))
