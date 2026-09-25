from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from ..domain import JsonObject, Manifest, Workflow
from ..errors import OwlError
from ..ports import SchemaValidator
from ..serialization import json_object, yaml_model
from .files import private_directory, safe_path


class FileBundles:
    def __init__(self, root: Path, validator: SchemaValidator) -> None:
        self.root = private_directory(root / "bundles")
        self.validator = validator

    def files(self, root: Path) -> list[Path]:
        if root.is_symlink():
            raise OwlError("SYMLINK", "Workflow roots cannot be symlinks")
        files: list[Path] = []
        size = 0
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(
                d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__", ".owlmatic"}
            )
            for entry in dirs + sorted(names):
                path = Path(directory) / entry
                if path.is_symlink():
                    raise OwlError("SYMLINK", "Workflow bundles cannot contain symlinks")
                if path.is_file():
                    amount = path.stat().st_size
                    size += amount
                    files.append(path)
                    if len(files) > 256 or size > 16 * 1024**2 or amount > 4 * 1024**2:
                        raise OwlError(
                            "BUNDLE_TOO_LARGE", "Bundle limit: 256 files, 16 MiB total, 4 MiB per file"
                        )
                elif not path.is_dir():
                    raise OwlError("INVALID_FILE", "Bundles may contain only regular files and directories")
        return sorted(files)

    def digest(self, root: Path) -> str:
        value = hashlib.sha256()
        for file in self.files(root):
            name = file.relative_to(root).as_posix().encode()
            data = file.read_bytes()
            # Executability is significant for direct script entrypoints.
            mode = b"x" if file.stat().st_mode & 0o111 else b"-"
            value.update(str(len(name)).encode() + b":" + name + mode + str(len(data)).encode() + b":" + data)
        return value.hexdigest()

    def schema(self, root: Path, name: str) -> JsonObject:
        path = safe_path(root, name)
        if path.stat().st_size > 16384:
            raise OwlError("SCHEMA_TOO_LARGE", "Schemas must be at most 16 KiB")
        raw = path.read_bytes()
        result = json_object(raw)
        self.validator.check_schema(result)
        return result

    def load(self, directory: Path, catalog: str = "draft", revision: str | None = None) -> Workflow:
        root = directory.absolute()
        path = safe_path(root, "workflow.yaml")
        if path.stat().st_size > 32768:
            raise OwlError("MANIFEST_TOO_LARGE", "Manifest must be at most 32 KiB")
        raw = path.read_bytes()
        manifest = yaml_model(raw, Manifest)
        for required_path in manifest.requirements.paths:
            safe_path(root, required_path)
        if len(set(manifest.verification.required_checks)) != len(manifest.verification.required_checks):
            raise OwlError("DUPLICATE_CHECK", "Required check names must be unique")
        input_schema = self.schema(root, manifest.input_schema)
        output_schema = self.schema(root, manifest.output_schema)
        checksum = self.digest(root)
        return Workflow(
            ref=f"{catalog}/{manifest.id}@{checksum}",
            catalog=catalog,
            id=manifest.id,
            digest=checksum,
            directory=root,
            manifest=manifest,
            input_schema=input_schema,
            output_schema=output_schema,
            source_revision=revision,
        )

    def snapshot(self, workflow: Workflow) -> Workflow:
        target = self.root / workflow.digest
        if not target.exists():
            temporary = Path(tempfile.mkdtemp(dir=self.root))
            try:
                for file in self.files(workflow.directory):
                    dest = temporary / file.relative_to(workflow.directory)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, dest)
                if self.digest(temporary) != workflow.digest:
                    raise OwlError("CONTENT_CHANGED", "Workflow changed while it was snapshotted")
                try:
                    temporary.rename(target)
                except OSError:
                    if not target.exists():
                        raise
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        result = Workflow(
            ref=workflow.ref,
            catalog=workflow.catalog,
            id=workflow.id,
            digest=workflow.digest,
            directory=target,
            manifest=workflow.manifest,
            input_schema=workflow.input_schema,
            output_schema=workflow.output_schema,
            source_revision=workflow.source_revision,
        )
        self.verify(result)
        return result

    def verify(self, workflow: Workflow) -> None:
        if self.digest(workflow.directory) != workflow.digest:
            raise OwlError("CONTENT_CHANGED", "Executable bundle no longer matches its selected digest")

    def discover(self, directory: Path) -> list[Path]:
        if directory.is_symlink() or not directory.is_dir():
            raise OwlError("INVALID_CATALOG", "Catalog root must be an existing directory, not a symlink")
        result: list[Path] = []
        for current, dirs, names in os.walk(directory):
            path = Path(current)
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__"}
            )
            if any((path / d).is_symlink() for d in dirs):
                raise OwlError("SYMLINK", "Catalog directories cannot be symlinks")
            if len(path.relative_to(directory).parts) > 8:
                dirs[:] = []
            elif "workflow.yaml" in names:
                result.append(path)
                dirs[:] = []
        return result
