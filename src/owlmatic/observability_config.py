"""Strict YAML configuration, independent of workflow manifests and trust settings."""

from ipaddress import ip_address
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .domain import Contract
from .export_contracts import ExportScope


class ExportAuth(Contract):
    token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @field_validator("token_env")
    @classmethod
    def credential_binding(cls, value: str | None) -> str | None:
        if value and (
            value in {"PATH", "HOME", "SHELL", "ENV", "BASH_ENV", "OWLMATIC_HOME"}
            or value.startswith(("PYTHON", "LD_", "DYLD_"))
        ):
            raise ValueError("Use a dedicated credential environment variable")
        return value


class HttpDestination(Contract):
    type: Literal["http"] = "http"
    endpoint: str = Field(min_length=1, max_length=2048)
    auth: ExportAuth = Field(default_factory=ExportAuth)

    @field_validator("endpoint")
    @classmethod
    def safe_endpoint(cls, value: str) -> str:
        if not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError("Use an ASCII URL without whitespace")
        url = urlsplit(value)
        if (
            not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Endpoint cannot contain credentials, query parameters, or fragments")
        if url.port is not None and url.port < 1:
            raise ValueError("Endpoint port must be positive")
        if url.scheme == "https":
            return value
        if url.scheme == "http":
            try:
                if ip_address(url.hostname).is_loopback:
                    return value
            except ValueError:
                pass
        raise ValueError("Use HTTPS, or HTTP with a literal loopback IP")


class DeliveryPolicy(Contract):
    timeout_seconds: int = Field(default=5, ge=1, le=30)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_initial_seconds: int = Field(default=2, ge=1, le=300)
    retry_max_seconds: int = Field(default=300, ge=1, le=3600)
    pending_retention_days: int = Field(default=7, ge=1, le=30)
    max_pending_bytes: int = Field(default=1048576, ge=1024, le=1048576)


class ExportConfiguration(Contract):
    mode: Literal["disabled", "manual", "after_workflow"] = "disabled"
    window_days: int = Field(default=30, ge=1, le=3650)
    destination: HttpDestination | None = None
    include: ExportScope = Field(default_factory=ExportScope)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)

    @model_validator(mode="after")
    def configured_destination(self) -> Self:
        if self.mode != "disabled" and self.destination is None:
            raise ValueError("Enabled export requires a destination")
        return self


class LocalDashboard(Contract):
    enabled: bool = False


class DashboardConfiguration(Contract):
    local: LocalDashboard = Field(default_factory=LocalDashboard)


class ObservabilityConfiguration(Contract):
    version: Literal[1] = 1
    dashboard: DashboardConfiguration = Field(default_factory=DashboardConfiguration)
    export: ExportConfiguration = Field(default_factory=ExportConfiguration)
