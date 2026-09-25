"""Private YAML configuration and a bounded single-slot JSON outbox."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml

from ..domain import Failure
from ..errors import OwlError
from ..export_state import ExportState
from ..observability_config import ObservabilityConfiguration
from ..serialization import atomic_model, atomic_text, read_model, yaml_model
from .files import private_directory


class FileExportStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "observability.yaml"
        self.state_path = root / "export-state.json"

    @contextmanager
    def locked(self) -> Iterator[None]:
        private_directory(self.root)
        with (self.root / "export.lock").open("a") as lock:
            (self.root / "export.lock").chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def configuration(self) -> ObservabilityConfiguration:
        return self.read(self.path) if self.path.exists() else ObservabilityConfiguration()

    def read(self, path: Path) -> ObservabilityConfiguration:
        if path.stat().st_size > 65536:
            raise OwlError("EXPORT_CONFIG", "Observability YAML must be at most 64 KiB")
        try:
            return yaml_model(path.read_bytes(), ObservabilityConfiguration)
        except (ValueError, OwlError) as error:
            raise OwlError(
                "EXPORT_CONFIG", "Invalid observability YAML; check fields, mode, and endpoint"
            ) from error

    def install(self, path: Path) -> ObservabilityConfiguration:
        config = self.read(path)
        atomic_text(
            self.path, yaml.safe_dump(config.model_dump(mode="json", exclude_none=True), sort_keys=False)
        )
        return config

    def disable(self) -> None:
        # A malformed configuration must still be possible to disable safely.
        try:
            previous = self.configuration()
        except OwlError:
            previous = ObservabilityConfiguration()
        disabled = ObservabilityConfiguration(
            version=previous.version, dashboard=previous.dashboard, measurement=previous.measurement
        )
        atomic_text(
            self.path, yaml.safe_dump(disabled.model_dump(mode="json", exclude_none=True), sort_keys=False)
        )

    def load(self) -> ExportState:
        if not self.state_path.exists():
            return ExportState()
        if self.state_path.stat().st_size > 2_097_152:
            raise OwlError("EXPORT_STATE", "Export state exceeds its size limit")
        return read_model(self.state_path, ExportState)

    def save(self, state: ExportState) -> None:
        atomic_model(self.state_path, state)

    def diagnostic(self, failure: Failure) -> None:
        atomic_model(self.root / "export-diagnostic.json", failure)
