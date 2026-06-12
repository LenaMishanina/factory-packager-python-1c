from __future__ import annotations

import asyncio
import base64

from app.schemas import ErpEventType
from app.services import erp_client as erp_client_module
from app.services.erp_client import ErpClient


def run(coro):
    return asyncio.run(coro)


def test_send_event_is_skipped_when_base_url_is_not_configured() -> None:
    result = run(ErpClient().send_event(ErpEventType.production_unit_start, {"event_id": "EVT-1"}))

    assert result == {"status": "skipped", "reason": "ERP_BASE_URL is not configured"}


def test_basic_auth_header_uses_utf8_credentials() -> None:
    client = ErpClient(username="Администратор", password="")
    expected_token = base64.b64encode("Администратор:".encode("utf-8")).decode("ascii")

    assert client.auth_header == f"Basic {expected_token}"


def test_send_event_uses_expected_erp_paths_and_headers(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, url: str, payload: dict) -> None:
            self.url = url
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"status": "ok", "url": self.url, "payload": self.payload}

    class FakeAsyncClient:
        instances = []

        def __init__(self, *, timeout: float, headers: dict, trust_env: bool) -> None:
            self.timeout = timeout
            self.headers = headers
            self.trust_env = trust_env
            self.posts = []
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, json: dict) -> FakeResponse:
            self.posts.append((url, json))
            return FakeResponse(url, json)

    monkeypatch.setattr(erp_client_module.httpx, "AsyncClient", FakeAsyncClient)
    client = ErpClient("http://example.test/zavodhttp/hs/", username="user", password="pass", timeout=12)

    production = run(client.send_event(ErpEventType.production_unit_start, {"event_id": "EVT-1"}))
    completion = run(client.send_event(ErpEventType.customer_order_complete, {"event_id": "EVT-2"}))

    assert production["url"] == "http://example.test/zavodhttp/hs/factory/production-unit/start"
    assert completion["url"] == "http://example.test/zavodhttp/hs/factory/customer-order/complete"
    assert FakeAsyncClient.instances[0].timeout == 12
    assert FakeAsyncClient.instances[0].trust_env is False
    assert FakeAsyncClient.instances[0].headers["Authorization"] == client.auth_header
