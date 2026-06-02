from __future__ import annotations

from typing import Any

import httpx

from app.schemas import ErpEventType


class ErpClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout = timeout

    async def send_event(self, event_type: ErpEventType, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "skipped", "reason": "ERP_BASE_URL is not configured"}

        path = {
            ErpEventType.production_unit_start: "/factory/production-unit/start",
            ErpEventType.customer_order_complete: "/factory/customer-order/complete",
        }[event_type]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()
