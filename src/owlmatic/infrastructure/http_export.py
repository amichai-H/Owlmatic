"""HTTP delivery without redirects, ambient proxies, or response-body logging."""

import http.client
import os
import ssl
from urllib.parse import urlsplit

from ..errors import OwlError
from ..observability_config import ExportConfiguration


class HttpSnapshotSender:
    def send(self, configuration: ExportConfiguration, snapshot_id: str, body: bytes) -> None:
        destination = configuration.destination
        if destination is None:
            raise OwlError("EXPORT_CONFIG", "No export destination configured")
        url = urlsplit(destination.endpoint)
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": snapshot_id,
            "User-Agent": "Owlmatic/0.1",
        }
        if destination.auth.token_env:
            token = os.environ.get(destination.auth.token_env)
            if not token or not token.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
                raise OwlError("EXPORT_AUTH", "The configured export token is missing or invalid")
            headers["Authorization"] = f"Bearer {token}"
        assert url.hostname
        connection = (
            http.client.HTTPSConnection(
                url.hostname,
                url.port,
                timeout=configuration.delivery.timeout_seconds,
                context=ssl.create_default_context(),
            )
            if url.scheme == "https"
            else http.client.HTTPConnection(
                url.hostname, url.port, timeout=configuration.delivery.timeout_seconds
            )
        )
        try:
            connection.request("POST", url.path or "/", body=body, headers=headers)
            response = connection.getresponse()
            if 200 <= response.status < 300:
                return
            if response.status in {408, 429} or response.status >= 500:
                raise OwlError("EXPORT_TRANSIENT", "Receiver temporarily unavailable; snapshot retained")
            raise OwlError("EXPORT_REJECTED", f"Receiver returned HTTP {response.status}; snapshot retained")
        except (OSError, http.client.HTTPException) as error:
            raise OwlError("EXPORT_TRANSIENT", "Delivery failed; snapshot retained for retry") from error
        finally:
            connection.close()
