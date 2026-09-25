import asyncio
import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.types import TextContent
from pydantic import BaseModel

from owlmatic.domain import Failure
from owlmatic.failures import public_failure
from owlmatic.mcp_server import invoke


@pytest.mark.parametrize(
    "error,code",
    [
        (PermissionError("sensitive value"), "ACCESS_DENIED"),
        (FileNotFoundError("sensitive value"), "PATH_NOT_FOUND"),
        (OSError(errno.ENOSPC, "sensitive value"), "STORAGE_FULL"),
        (ValueError("sensitive value"), "INVALID_REQUEST"),
        (RuntimeError("sensitive value"), "INTERNAL_ERROR"),
    ],
)
def test_errors_do_not_expose_exception_payloads(error: Exception, code: str) -> None:
    failure = public_failure(error)
    assert failure.code == code
    assert "sensitive" not in failure.model_dump_json()


def test_corrupt_database_produces_json_cli_error(tmp_path: Path) -> None:
    (tmp_path / "catalog.sqlite").write_bytes(b"corrupt fixture")
    result = subprocess.run(
        [sys.executable, "-m", "owlmatic", "doctor", "--json"],
        env={**os.environ, "OWLMATIC_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert Failure.model_validate_json(result.stdout).code == "STORAGE_CORRUPT"
    assert "Traceback" not in result.stderr


def test_unexpected_mcp_errors_are_safe_json() -> None:
    def operation() -> BaseModel:
        raise RuntimeError("private credential fixture")

    result = asyncio.run(invoke(operation))
    assert result.isError and isinstance(result.content[0], TextContent)
    assert Failure.model_validate_json(result.content[0].text).code == "INTERNAL_ERROR"
    assert "credential" not in result.content[0].text


def test_worker_bootstrap_failure_retains_private_diagnostic(tmp_path: Path) -> None:
    run_id = "run_" + "f" * 32
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    (tmp_path / "catalog.sqlite").write_bytes(b"corrupt fixture")
    result = subprocess.run(
        [sys.executable, "-m", "owlmatic.worker", run_id],
        env={**os.environ, "OWLMATIC_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert not result.stdout and "Traceback" not in result.stderr
    artifact = directory / "diagnostic.json"
    assert artifact.stat().st_size < 1024
    assert artifact.stat().st_mode & 0o077 == 0
    assert Failure.model_validate_json(artifact.read_bytes()).code == "STORAGE_CORRUPT"


def test_mcp_bootstrap_error_preserves_protocol(tmp_path: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    (tmp_path / "catalog.sqlite").write_bytes(b"corrupt fixture")

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "owlmatic", "mcp", "serve"],
            env={**os.environ, "OWLMATIC_HOME": str(tmp_path)},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 5
                response = await session.call_tool("owlmatic_find", {"query": "fixture"})
                assert response.isError and isinstance(response.content[0], TextContent)
                assert Failure.model_validate_json(response.content[0].text).code == "STORAGE_CORRUPT"

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))
