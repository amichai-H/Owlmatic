"""Applicability and permission decisions, independent of CLI/MCP and storage implementation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from .domain import JsonObject, Profile, Workflow
from .errors import OwlError
from .ports import BundleStore, Host, SchemaValidator, SettingsStore
from .serialization import payload_json


@dataclass(frozen=True)
class ExecutionEnvironment:
    values: Mapping[str, str]
    secrets: tuple[str, ...]


def get_profile(settings: SettingsStore, name: str) -> Profile:
    value = settings.load().profiles.get(name)
    if value is None:
        raise OwlError("INVALID_PROFILE", f"Unknown profile: {name}")
    return value


def environment_for(workflow: Workflow, profile: Profile, ambient: Mapping[str, str]) -> ExecutionEnvironment:
    values = {
        "PATH": ambient.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    secrets: set[str] = set()
    for name in profile.env:
        if name.startswith("OWLMATIC_") or name in {"PYTHONPATH", "PYTHONHOME"}:
            raise OwlError("INVALID_PROFILE", "Reserved environment names cannot be inherited")
        if name in ambient:
            values[name] = ambient[name]
            if any(word in name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "KEY")):
                secrets.add(ambient[name])
    names: set[str] = set()
    for binding in workflow.manifest.credentials:
        source = profile.credentials.get(binding)
        if not source or not ambient.get(source):
            raise OwlError("MISSING_CREDENTIAL", f"Missing credential binding: {binding}")
        target = "OWLMATIC_CREDENTIAL_" + re.sub(r"[^A-Z0-9]", "_", binding.upper())
        if target in names:
            raise OwlError("INVALID_CREDENTIAL", "Credential binding names collide after normalization")
        names.add(target)
        values[target] = ambient[source]
        secrets.add(ambient[source])
    return ExecutionEnvironment(
        values=values, secrets=tuple(sorted((s for s in secrets if s), key=len, reverse=True))
    )


@dataclass(frozen=True)
class ExecutionPolicy:
    settings: SettingsStore
    bundles: BundleStore
    schemas: SchemaValidator
    host: Host

    def check(
        self,
        workflow: Workflow,
        inputs: JsonObject,
        profile_name: str,
        environment: str,
        workspace: Path,
        *,
        validation: bool = False,
    ) -> Profile:
        p = get_profile(self.settings, profile_name)
        if not validation and workflow.ref not in p.grants:
            raise OwlError(
                "UNTRUSTED", "Review and explicitly trust the exact workflow reference before running"
            )
        if len(payload_json(inputs).encode()) > 16384:
            raise OwlError("INPUT_TOO_LARGE", "Inputs must be at most 16 KiB")
        self.schemas.validate(workflow.input_schema, inputs, "INVALID_INPUT")
        m = workflow.manifest
        if environment not in m.environments or environment not in p.environments:
            raise OwlError(
                "ENVIRONMENT_DENIED", "Environment must be allowed by both the workflow and profile"
            )
        if environment == "production" and profile_name == "default":
            raise OwlError(
                "PRODUCTION_PROFILE_REQUIRED", "Production requires a separately configured profile"
            )
        if not self.host.workspace_exists(workspace):
            raise OwlError("MISSING_WORKSPACE", "Target workspace must exist")
        self.bundles.verify(workflow)
        requirements = m.requirements
        if requirements.platforms and self.host.platform not in requirements.platforms:
            raise OwlError("INCOMPATIBLE_PLATFORM", "Workflow does not support this platform")
        if requirements.repository and workspace.name != requirements.repository:
            raise OwlError("WRONG_REPOSITORY", "Workspace name does not match the declared repository")
        for path in requirements.paths:
            if not self.host.required_path_exists(workspace, path):
                raise OwlError("MISSING_PATH", f"Missing required workspace path: {path}")
        selected = environment_for(workflow, p, self.host.environment)
        for command in (m.entrypoint[0], *requirements.commands):
            if not self.host.command_exists(command, workflow.directory, selected.values["PATH"]):
                raise OwlError("MISSING_COMMAND", f"Missing executable: {command}")

        def has_secret(value: JsonValue) -> bool:
            if isinstance(value, str):
                return any(secret in value for secret in selected.secrets)
            if isinstance(value, list):
                return any(has_secret(item) for item in value)
            if isinstance(value, dict):
                return any(has_secret(key) or has_secret(item) for key, item in value.items())
            return False

        if has_secret(inputs):
            raise OwlError(
                "SECRET_INPUT", "Use credential bindings instead of putting secret values in inputs"
            )
        return p
