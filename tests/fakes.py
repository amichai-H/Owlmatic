from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from owlmatic.domain import Settings, Target, Workflow


@dataclass
class FakeClock:
    now: float = 1000.0

    def epoch(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def iso(self) -> str:
        return f"fake-time-{self.now}"

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class MemorySettings:
    value: Settings = field(default_factory=Settings)

    def load(self) -> Settings:
        return self.value

    def save(self, settings: Settings) -> None:
        self.value = settings

    def update(self, change: Callable[[Settings], Settings]) -> Settings:
        self.value = change(self.value)
        return self.value


@dataclass
class FakeHost:
    environment: Mapping[str, str] = field(default_factory=dict)
    platform: str = "linux"
    has_command: bool = True
    has_workspace: bool = True
    has_path: bool = True

    def command_exists(self, command: str, directory: Path, path: str) -> bool:
        return self.has_command

    def target(self, workspace: Path, environment: str) -> Target:
        return Target(
            environment=environment, workspace=str(workspace), source_revision="fixture-revision", dirty=False
        )

    def workspace_exists(self, workspace: Path) -> bool:
        return self.has_workspace

    def required_path_exists(self, workspace: Path, path: str) -> bool:
        return self.has_path


@dataclass
class FakeBundles:
    workflow: Workflow
    verified: int = 0

    def load(self, directory: Path, catalog: str = "draft", revision: str | None = None) -> Workflow:
        return self.workflow

    def snapshot(self, workflow: Workflow) -> Workflow:
        return workflow

    def verify(self, workflow: Workflow) -> None:
        self.verified += 1

    def discover(self, directory: Path) -> tuple[Path, ...]:
        return (self.workflow.directory,)
