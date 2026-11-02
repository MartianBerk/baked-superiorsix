from __future__ import annotations

from typing import Any

import httpx


class HttpxJsonClient:
    """Thin sync JSON GET wrapper used by ingest connectors."""

    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        finally:
            if self._owns_client and self._client is None:
                client.close()
