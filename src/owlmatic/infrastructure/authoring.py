from __future__ import annotations

import re
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from ..domain import Execution, Fixture, JsonObject, Manifest, Verification
from ..errors import OwlError
from ..messages import DraftReport, SetupReport
from ..serialization import atomic_model, atomic_text, payload_json
from .files import private_directory, safe_path


def resource(name: str) -> Path:
    packaged = Path(__file__).resolve().parents[1] / "resources" / name
    if packaged.exists():
        return packaged
    root = Path(__file__).resolve().parents[3]
    return root / ("skills/owlmatic" if name == "skill" else name)


SCAFFOLD = """import json
import os
from pathlib import Path

inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
# Replace this disposable example with the procedure and independent assertions.
healthy = inputs["healthy"]
result = {
    "run_id": os.environ["OWLMATIC_RUN_ID"],
    "outcome": "pass" if healthy else "fail",
    "checks": [{"name": "example_check", "status": "pass" if healthy else "fail"}],
    "outputs": {"healthy": healthy},
    "effects": "none",
}
Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))
"""


class FileDrafts:
    def __init__(self, root: Path) -> None:
        self.root = private_directory(root / "drafts")

    def create(self, name: str, output: Path | None) -> DraftReport:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", name):
            raise OwlError(
                "INVALID_NAME", "Use a lowercase workflow name with letters, digits, dots or hyphens"
            )
        directory = output or self.root / name
        if directory.exists():
            raise OwlError("DRAFT_EXISTS", "Draft destination already exists; existing work was not changed")
        directory.mkdir(parents=True, mode=0o700)
        manifest = Manifest(
            schema_version=1,
            id=name,
            version="0.1.0",
            description=f"Draft procedure: {name}",
            owner="local-author",
            entrypoint=("python3", "run.py"),
            input_schema="input.schema.json",
            output_schema="output.schema.json",
            environments=("simulation",),
            effects=(),
            execution=Execution(timeout_seconds=30),
            verification=Verification(required_checks=("example_check",)),
            tests=(
                Fixture(inputs={"healthy": True}, outcome="pass"),
                Fixture(inputs={"healthy": False}, outcome="fail"),
            ),
        )
        atomic_model(directory / "workflow.yaml", manifest)
        schema: JsonObject = {
            "type": "object",
            "additionalProperties": False,
            "required": ["healthy"],
            "properties": {"healthy": {"type": "boolean"}},
        }
        for filename in ("input.schema.json", "output.schema.json"):
            atomic_text(directory / filename, payload_json(schema))
        atomic_text(directory / "run.py", SCAFFOLD)
        shutil.copyfile(resource("skill") / "references" / "authoring.md", directory / "AUTHORING.md")
        return DraftReport(
            draft=directory, next_step="Replace the example implementation and fixtures, then validate"
        )

    @contextmanager
    def workspace(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="owlmatic-test-") as directory:
            yield Path(directory)


class FileIntegrations:
    def setup(self, host: Literal["codex", "claude"], project: Path, apply: bool) -> SetupReport:
        if not project.is_dir():
            raise OwlError("MISSING_WORKSPACE", "Setup requires an existing project directory")
        root = resource("skill")
        relative = (".agents" if host == "codex" else ".claude") + "/skills/owlmatic"
        destination = safe_path(project, relative)
        sources = [
            (source, safe_path(destination, source.relative_to(root).as_posix()))
            for source in root.rglob("*")
            if source.is_file()
        ]
        for source, target in sources:
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise OwlError(
                    "SETUP_CONFLICT", "An existing integration file differs; it was not overwritten"
                )
        if apply:
            for source, target in sources:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        executable = shutil.which("owlmatic") or str(Path(sys.executable).parent / "owlmatic")
        command = (
            ("codex", "mcp", "add", "owlmatic", "--", executable, "mcp", "serve")
            if host == "codex"
            else (
                "claude",
                "mcp",
                "add",
                "--transport",
                "stdio",
                "--scope",
                "project",
                "owlmatic",
                "--",
                executable,
                "mcp",
                "serve",
            )
        )
        return SetupReport(
            host=host,
            skill=destination,
            installed=apply,
            mcp_command=command,
            note="Run MCP registration from the project directory; host settings were not rewritten",
        )
