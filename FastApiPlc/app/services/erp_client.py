from __future__ import annotations

from typing import Any

import base64
import httpx

from app.schemas import ErpEventType


class ErpClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 180.0,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout = timeout
        self.auth_header = self._basic_auth_header(username, password) if username else None

    async def send_event(self, event_type: ErpEventType, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "skipped", "reason": "ERP_BASE_URL is not configured"}

        path = {
            ErpEventType.production_unit_start: "/factory/production-unit/start",
            ErpEventType.customer_order_complete: "/factory/customer-order/complete",
        }[event_type]

        headers = {}
        if self.auth_header:
            headers["Authorization"] = self.auth_header

        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, trust_env=False) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()

    def _basic_auth_header(self, username: str, password: str | None) -> str:
        credentials = f"{username}:{password or ''}".encode("utf-8")
        token = base64.b64encode(credentials).decode("ascii")
        return f"Basic {token}"
