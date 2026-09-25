from typing import Protocol

from .contracts import Environment, Incident, Observation, Result


class Runner(Protocol):
    def describe(self, timeout: float) -> Environment: ...
    def execute(self, incident: Incident, attempt: int, timeout: float) -> Observation: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...


class IncidentRepository(Protocol):
    def load(self, execution_id: str) -> Incident: ...


class EvidenceStore(Protocol):
    def snapshot(self, incident: Incident) -> None: ...
    def attempts(self, result: Result) -> None: ...
