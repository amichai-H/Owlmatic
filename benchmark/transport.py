"""Measure actual wire payloads without calling an agent or claiming token savings."""

import asyncio
import tempfile
from pathlib import Path

from owlmatic.bootstrap import create_application
from owlmatic.domain import Contract, RunRequest, SearchRequest
from owlmatic.infrastructure.authoring import resource
from owlmatic.mcp_server import create_server
from owlmatic.serialization import encode


class TransportSample(Contract):
    tool_count: int
    tool_definitions_bytes: int
    skill_bytes: int
    find_bytes: int
    run_bytes: int
    logs_bytes: int
    provider_tokens: int | None = None
    note: str = "UTF-8 payload bytes, not model tokens; does not include host prompts or protocol framing"


async def measure() -> TransportSample:
    with tempfile.TemporaryDirectory(prefix="owlmatic-benchmark-") as temporary:
        root = Path(temporary)
        app = create_application(root / "home")
        app.examples()
        found = app.catalog.find(SearchRequest(query="oauth rotation"))
        ref = found.results[0].ref
        app.catalog.trust(ref)
        result = await asyncio.to_thread(
            app.execution.run, RunRequest(ref=ref, workspace=root, inputs={"healthy": True})
        )
        if result.run.outcome != "pass":
            raise RuntimeError("Benchmark fixture did not pass")
        tools = await create_server(app).list_tools()
        logs = root / "home/runs" / result.run.run_id / "events.jsonl"
        return TransportSample(
            tool_count=len(tools),
            tool_definitions_bytes=sum(len(tool.model_dump_json().encode()) for tool in tools),
            skill_bytes=len((resource("skill") / "SKILL.md").read_bytes()),
            find_bytes=len(encode(found).encode()),
            run_bytes=len(encode(result).encode()),
            logs_bytes=logs.stat().st_size,
        )


if __name__ == "__main__":
    print(asyncio.run(measure()).model_dump_json(indent=2))
