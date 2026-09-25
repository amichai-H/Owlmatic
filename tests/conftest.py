from pathlib import Path

import pytest

from owlmatic.domain import Workflow
from owlmatic.infrastructure.bundles import FileBundles
from owlmatic.infrastructure.schema import JsonSchemaValidator

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workflow(tmp_path: Path) -> Workflow:
    return FileBundles(tmp_path, JsonSchemaValidator()).load(ROOT / "examples/e2e", "examples")
