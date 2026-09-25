"""Start either mock API, bound exclusively to loopback. No real deployment integration."""

from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .fixtures import Plan, log_page, status
from .store import Store
from .workflow.monitoring.contracts import DeployRequest, LogQuery


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer otherwise performs a reverse-DNS lookup during startup.
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]


def handler(store: Store, plan: Plan, kind: Literal["deploy", "logs"]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def _handle(self) -> None:
            self.connection.settimeout(3)
            try:
                payload = self._dispatch()
                self._reply(200, payload)
            except KeyError:
                self._reply(404, b'{"error":"not_found"}')
            except (ValueError, ValidationError):
                self._reply(400, b'{"error":"invalid_request_or_key_conflict"}')
            except OSError:
                self.close_connection = True

        def _dispatch(self) -> bytes:
            path = urlsplit(self.path)
            if self.command == "GET" and path.path == "/health":
                return b'{"status":"ready"}'
            if kind == "deploy" and self.command == "POST" and path.path == "/deployments":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("body size")
                request = DeployRequest.model_validate_json(self.rfile.read(length))
                return store.deploy(request, plan, time.time()).model_dump_json().encode()
            if kind == "deploy" and self.command == "GET" and path.path.startswith("/deployments/"):
                return (
                    status(store.get(path.path.removeprefix("/deployments/")), time.time())
                    .model_dump_json()
                    .encode()
                )
            if kind == "logs" and self.command == "GET" and path.path == "/logs":
                params = parse_qs(path.query, strict_parsing=True)
                query = LogQuery(
                    deployment_id=params["deployment_id"][0],
                    start=float(params["start"][0]),
                    end=float(params["end"][0]),
                    cursor=int(params["cursor"][0]),
                )
                if query.end < query.start or query.end - query.start > 301:
                    raise ValueError("interval")
                record = store.get(query.deployment_id)
                if record.plan.fault == "unavailable":
                    raise KeyError("log service unavailable")
                if record.plan.fault == "malformed":
                    return b'{"events": "broken"}'
                return log_page(record, query, time.time()).model_dump_json().encode()
            raise KeyError(path.path)

        def _reply(self, code: int, payload: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("deploy", "logs"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()
    plan = Plan.model_validate_json(args.plan.read_bytes())
    with LoopbackServer(("127.0.0.1", args.port), handler(Store(args.database), plan, args.kind)) as server:
        args.ready.write_text(str(server.server_port))
        server.serve_forever()


if __name__ == "__main__":
    main()
