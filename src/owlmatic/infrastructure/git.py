from __future__ import annotations

import fcntl
import hashlib
import io
import tarfile
import tempfile
from pathlib import Path

from ..domain import Catalog, Workflow
from ..errors import OwlError
from ..ports import BundleStore, CommandExecutor
from .files import private_directory, safe_path


class FileCatalogSource:
    def __init__(self, root: Path, bundles: BundleStore, commands: CommandExecutor) -> None:
        self.root = private_directory(root / "catalogs")
        self.bundles = bundles
        self.commands = commands

    def read(self, catalog: Catalog) -> tuple[list[Workflow], str | None]:
        if catalog.kind == "local":
            return [
                self.bundles.snapshot(self.bundles.load(p, catalog.name))
                for p in self.bundles.discover(Path(catalog.source))
            ], None
        key = hashlib.sha256(catalog.source.encode()).hexdigest()
        with (self.root / (key + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._read_git(catalog, self.root / key)

    def _read_git(self, catalog: Catalog, repo: Path) -> tuple[list[Workflow], str | None]:
        git = ["git", "-c", "core.hooksPath=/dev/null"]
        if not repo.exists():
            self.commands.execute([*git, "init", "--bare", str(repo)])
        base = [*git, "-C", str(repo)]
        self.commands.execute([*base, "fetch", "--force", "--", catalog.source, catalog.revision or "HEAD"])
        commit = self.commands.execute([*base, "rev-parse", "FETCH_HEAD^{commit}"]).decode().strip()
        archive = self.commands.execute([*base, "archive", "--format=tar", commit])
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            root = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                for member in tar:
                    target = safe_path(root, member.name)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        stream = tar.extractfile(member)
                        if stream is not None:
                            target.write_bytes(stream.read())
                            target.chmod(member.mode & 0o700)
                    else:
                        raise OwlError(
                            "INVALID_ARCHIVE", "Git catalogs cannot contain links or special files"
                        )
            workflows = [
                self.bundles.snapshot(self.bundles.load(p, catalog.name, commit))
                for p in self.bundles.discover(root)
            ]
        return workflows, commit
