import shutil
import subprocess
from pathlib import Path

import pytest

from owlmatic.bootstrap import create_application
from owlmatic.domain import Catalog, SearchRequest
from owlmatic.errors import OwlError
from tests.conftest import ROOT


def git(directory: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


def test_git_catalog_pins_content_and_retains_previous_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(ROOT / "examples/e2e", source / "e2e")
    git(source, "init")
    git(source, "add", ".")
    git(source, "commit", "-m", "fixture")
    first_commit = git(source, "rev-parse", "HEAD")
    app = create_application(tmp_path / "home")
    report = app.catalog.add(Catalog(name="team", kind="git", source=str(source), revision=first_commit))
    assert report.revision == first_commit
    first = app.catalog.find(SearchRequest(query="example.e2e")).results[0]
    app.catalog.trust(first.ref)
    (source / "e2e/run.py").write_text("print('updated')\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "changed")
    app.catalog.sync("team")
    assert app.catalog.find(SearchRequest(query="example.e2e")).results[0].ref == first.ref
    # A moving HEAD catalog observes changes but cannot reuse the other catalog's grant.
    app.catalog.add(Catalog(name="head", kind="git", source=str(source)))
    changed = app.catalog.find(SearchRequest(query="example.e2e")).results
    assert any(item.ref.startswith("head/") and item.trust == "untrusted" for item in changed)


def test_git_archive_rejects_links_without_following_them(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "external").symlink_to("/etc/passwd")
    git(source, "init")
    git(source, "add", ".")
    git(source, "commit", "-m", "link fixture")
    app = create_application(tmp_path / "home")
    with pytest.raises(OwlError) as caught:
        app.catalog.add(Catalog(name="links", kind="git", source=str(source)))
    assert caught.value.code == "INVALID_ARCHIVE"
    assert not app.catalog.list().catalogs
