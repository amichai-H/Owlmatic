"""The only untyped JSON/YAML decoding boundary; outputs are validated immediately."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from .domain import JsonObject
from .errors import OwlError

T = TypeVar("T", bound=BaseModel)
JSON_OBJECT = TypeAdapter(JsonObject)


def json_object(raw: str | bytes) -> JsonObject:
    return JSON_OBJECT.validate_json(raw, strict=True)


def encode(value: BaseModel) -> str:
    return value.model_dump_json(exclude_none=True)


def payload_json(value: JsonObject) -> str:
    return JSON_OBJECT.dump_json(value).decode()


def read_model(path: Path, model: type[T]) -> T:
    return model.model_validate_json(path.read_bytes())


def atomic_text(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_model(path: Path, value: BaseModel) -> None:
    atomic_text(path, encode(value) + "\n")


def yaml_model(raw: bytes, model: type[T]) -> T:
    import yaml

    # JSON round trip is an intentional dynamic input boundary. Strict JSON parsing
    # admits arrays for immutable tuple fields without admitting scalar coercion.
    try:
        return model.model_validate_json(json.dumps(yaml.safe_load(raw), allow_nan=False))
    except (yaml.YAMLError, ValueError, TypeError) as error:
        raise OwlError("INVALID_MANIFEST", "Manifest does not conform to the workflow contract") from error
