from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .domain import Profile, Settings
from .errors import OwlError
from .messages import ProfileReport, SetupReport
from .ports import SettingsStore


class IntegrationInstaller(Protocol):
    def setup(self, host: Literal["codex", "claude"], project: Path, apply: bool) -> SetupReport: ...


@dataclass(frozen=True)
class Administration:
    settings: SettingsStore
    integrations: IntegrationInstaller

    def create_profile(
        self, name: str, environments: tuple[str, ...], env: tuple[str, ...], bindings: tuple[str, ...]
    ) -> ProfileReport:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", name) or not environments:
            raise OwlError("INVALID_PROFILE", "Supply a profile name and at least one environment")
        credentials: dict[str, str] = {}
        for binding in bindings:
            key, separator, value = binding.partition("=")
            if not separator or not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise OwlError("INVALID_CREDENTIAL", "Credential bindings use NAME=ENVIRONMENT_VARIABLE")
            credentials[key] = value

        def change(settings: Settings) -> Settings:
            if name in settings.profiles:
                raise OwlError("PROFILE_EXISTS", "Profile exists; edit its local configuration explicitly")
            return settings.with_profile(
                name, Profile(environments=environments, env=env, credentials=credentials)
            )

        self.settings.update(change)
        return ProfileReport(profile=name, environments=environments)

    def setup(self, host: Literal["codex", "claude"], project: Path, apply: bool = False) -> SetupReport:
        return self.integrations.setup(host, project, apply)
