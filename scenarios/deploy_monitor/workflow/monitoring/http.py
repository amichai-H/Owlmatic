"""Bounded, loopback-only HTTP transport for this simulation, never production credentials."""

import time
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlencode, urlsplit

from pydantic import ValidationError

from .contracts import DeploymentStatus, DeployRequest, LogPage, LogQuery, Receipt, Unavailable


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def local_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "http"
        or parts.hostname != "127.0.0.1"
        or not parts.port
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Simulation endpoints must be http://127.0.0.1:<port>")
    return value.rstrip("/")


class LocalHTTP:
    def __init__(self, base_url: str) -> None:
        self.base_url = local_url(base_url)

    def request(self, path: str, body: bytes | None = None) -> bytes:
        # HTTPConnection neither inherits proxies nor follows redirects.
        connection = HTTPConnection("127.0.0.1", urlsplit(self.base_url).port, timeout=2)
        try:
            connection.request(
                "POST" if body is not None else "GET",
                path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise Unavailable("upstream HTTP status is not 200")
            data = response.read(65537)
            if len(data) > 65536:
                raise Unavailable("response exceeds 64 KiB")
            return data
        except (HTTPException, TimeoutError, OSError) as exc:
            raise Unavailable("upstream HTTP unavailable") from exc
        finally:
            connection.close()


class HTTPDeployments:
    def __init__(self, transport: LocalHTTP) -> None:
        self.transport = transport

    def deploy(self, request: DeployRequest) -> Receipt:
        try:
            return Receipt.model_validate_json(
                self.transport.request("/deployments", request.model_dump_json().encode())
            )
        except ValidationError as exc:
            raise Unavailable("invalid deployment receipt") from exc

    def status(self, deployment_id: str) -> DeploymentStatus:
        try:
            return DeploymentStatus.model_validate_json(
                self.transport.request("/deployments/" + deployment_id)
            )
        except ValidationError as exc:
            raise Unavailable("invalid deployment status") from exc


class HTTPLogs:
    def __init__(self, transport: LocalHTTP) -> None:
        self.transport = transport

    def read(self, query: LogQuery) -> LogPage:
        try:
            return LogPage.model_validate_json(
                self.transport.request("/logs?" + urlencode(query.model_dump()))
            )
        except ValidationError as exc:
            raise Unavailable("invalid log page") from exc
