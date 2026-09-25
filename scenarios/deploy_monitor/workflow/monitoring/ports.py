"""External dependencies; domain decisions do not depend on HTTP or wall clocks."""

from typing import Protocol

from .contracts import DeploymentStatus, DeployRequest, LogPage, LogQuery, Receipt


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class Deployments(Protocol):
    def deploy(self, request: DeployRequest) -> Receipt: ...
    def status(self, deployment_id: str) -> DeploymentStatus: ...


class Logs(Protocol):
    def read(self, query: LogQuery) -> LogPage: ...
