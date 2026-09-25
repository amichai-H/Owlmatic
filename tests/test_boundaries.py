import json
from pathlib import Path

import pytest

from owlmatic.domain import JsonObject, Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.bundles import FileBundles
from owlmatic.infrastructure.files import safe_path
from owlmatic.infrastructure.logs import RedactedLog
from owlmatic.infrastructure.schema import JsonSchemaValidator


@pytest.mark.parametrize("split", range(1, 22))
def test_secret_redaction_survives_every_chunk_boundary(tmp_path: Path, split: int) -> None:
    path = tmp_path / "log.jsonl"
    log = RedactedLog(path, ('secret-with-quotes"', "secret"), 4096)
    raw = b'before secret-with-quotes" after'
    log.write("stdout", raw[:split])
    log.write("stderr", b"independent stream", final=True)
    log.write("stdout", raw[split:], final=True)
    log.close()
    events = [json.loads(line) for line in path.read_text().splitlines()]
    text = "".join(event["text"] for event in events if event["stream"] == "stdout")
    assert "secret" not in text
    assert "[REDACTED]" in text
    assert "before " in text and " after" in text


def test_external_schema_reference_is_never_resolved() -> None:
    schema: JsonObject = {"type": "object", "$ref": "https://example.invalid/secrets"}
    validator = JsonSchemaValidator()
    with pytest.raises(OwlError) as caught:
        validator.check_schema(schema)
    assert caught.value.code == "REMOTE_SCHEMA"
    with pytest.raises(OwlError):
        validator.validate(schema, {}, "INVALID_INPUT")


@pytest.mark.parametrize("path", ["../escape", "/absolute"])
def test_path_traversal_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(OwlError):
        safe_path(tmp_path, path)


def test_snapshot_detects_tampering_and_rejects_links(tmp_path: Path, workflow: Workflow) -> None:
    bundles = FileBundles(tmp_path, JsonSchemaValidator())
    snapshot = bundles.snapshot(workflow)
    (snapshot.directory / "run.py").write_text("print('tampered')")
    with pytest.raises(OwlError) as caught:
        bundles.verify(snapshot)
    assert caught.value.code == "CONTENT_CHANGED"
    (snapshot.directory / "leak").symlink_to(tmp_path)
    with pytest.raises(OwlError) as caught:
        bundles.load(snapshot.directory)
    assert caught.value.code == "SYMLINK"
