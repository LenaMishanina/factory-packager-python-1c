from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.schemas import ErpEventType, LoadToHmiRequest, OrderLineRequest, OrderStatus
from app.services.orders import OrderService
from app.storage.json_store import JsonStore


def run(coro):
    return asyncio.run(coro)


def make_service(tmp_path: Path) -> OrderService:
    return OrderService(JsonStore(tmp_path / "orders.json"))


def make_order_request(
    *,
    number: str = "ORDER-1",
    color: str = "black",
    tags: int = 1,
    cap: bool = False,
    quantity: int = 1,
) -> LoadToHmiRequest:
    return LoadToHmiRequest(
        customer_order_number=number,
        items=[OrderLineRequest(line_number=1, color=color, tags=tags, cap=cap, quantity=quantity)],
    )


def test_load_to_hmi_creates_running_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        order = await service.load_to_hmi(make_order_request(quantity=2))

        assert order.customer_order_number == "ORDER-1"
        assert order.status == OrderStatus.running
        assert order.total_required == 2
        assert order.total_started == 0
        assert order.total_completed == 0

        active = await service.active_order()
        assert active is not None
        assert active.id == order.id

    run(scenario())


def test_load_to_hmi_rejects_second_active_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        await service.load_to_hmi(make_order_request(number="ORDER-1"))

        with pytest.raises(ValueError, match="Another customer order"):
            await service.load_to_hmi(make_order_request(number="ORDER-2"))

    run(scenario())


def test_handle_color_detected_accepts_matching_container(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        order = await service.load_to_hmi(make_order_request(tags=2, cap=True, quantity=2))

        operation, payload = await service.handle_color_detected(1)

        assert operation.decision == "accepted"
        assert operation.color == "black"
        assert operation.unit_sequence == 1
        assert operation.tags == 2
        assert operation.cap is True
        assert payload is not None
        assert payload["event_id"].startswith("EVT-")
        assert payload["fastapi_order_id"] == order.id
        assert payload["customer_order_number"] == "ORDER-1"
        assert payload["color"] == "black"
        assert payload["tags"] == 2
        assert payload["cap"] is True

        active = await service.active_order()
        assert active is not None
        assert active.recipes["black"].started == 1
        assert active.active_operation is not None

    run(scenario())


def test_handle_color_detected_rejects_unneeded_color_without_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        await service.load_to_hmi(make_order_request(color="black"))

        operation, payload = await service.handle_color_detected(2)

        assert operation.decision == "rejected"
        assert operation.color == "red"
        assert operation.reject_reason == "color_not_in_customer_order"
        assert payload is None

        active = await service.active_order()
        assert active is not None
        assert active.active_operation is not None

    run(scenario())


def test_handle_color_detected_rejects_when_no_active_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)

        operation, payload = await service.handle_color_detected(1)

        assert operation.decision == "rejected"
        assert operation.reject_reason == "no_active_order"
        assert payload is None

    run(scenario())


def test_handle_color_detected_rejects_new_operation_when_previous_is_active(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        await service.load_to_hmi(make_order_request())
        await service.handle_color_detected(1)

        with pytest.raises(ValueError, match="Previous container"):
            await service.handle_color_detected(1)

    run(scenario())


def test_handle_finished_tray_completes_order_and_creates_single_completion_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        order = await service.load_to_hmi(make_order_request(quantity=1))
        await service.handle_color_detected(1)

        payload = await service.handle_finished_tray()
        repeated_payload = await service.handle_finished_tray()
        completed = await service.get_order(order.id)

        assert payload is not None
        assert payload["event_id"].startswith("EVT-")
        assert payload["fastapi_order_id"] == order.id
        assert payload["customer_order_number"] == "ORDER-1"
        assert payload["completed"] == {"black": 1}
        assert payload["rejected_count"] == 0
        assert repeated_payload is None
        assert completed is not None
        assert completed.status == OrderStatus.completed
        assert completed.total_completed == 1
        assert completed.active_operation is None
        assert completed.completion_notified is True
        assert await service.active_order() is None

    run(scenario())


def test_handle_reject_tray_counts_rejected_container_and_clears_operation(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        await service.load_to_hmi(make_order_request(color="black"))
        await service.handle_color_detected(2)

        await service.handle_reject_tray()

        active = await service.active_order()
        data = await service.store.load()
        assert active is not None
        assert active.rejected_count == 1
        assert active.active_operation is None
        assert len(data["rejects"]) == 1
        assert data["rejects"][0]["reject_reason"] == "color_not_in_customer_order"

    run(scenario())


def test_integration_event_retry_statuses(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        payload = {"event_id": "EVT-1", "customer_order_number": "ORDER-1"}

        event = await service.create_integration_event(
            ErpEventType.customer_order_complete,
            payload,
            max_attempts=2,
        )
        attempted = await service.register_integration_attempt(event.id, default_max_attempts=2)
        failed = await service.mark_integration_event_failed(
            event.id,
            "temporary error",
            retryable=True,
            retry_interval_seconds=10,
        )
        assert attempted is not None
        assert attempted.attempts == 1
        assert failed is not None
        assert failed.status == "retry_pending"
        assert failed.error == "temporary error"
        assert failed.next_retry_at is not None

        sent = await service.mark_integration_event_sent(event.id, "sent")
        assert sent is not None
        assert sent.status == "sent"
        assert sent.error is None
        assert sent.next_retry_at is None

        final_event = await service.create_integration_event(
            ErpEventType.customer_order_complete,
            payload,
            max_attempts=1,
        )
        await service.register_integration_attempt(final_event.id, default_max_attempts=1)
        permanent = await service.mark_integration_event_failed(
            final_event.id,
            "still failing",
            retryable=True,
            retry_interval_seconds=10,
        )
        assert permanent is not None
        assert permanent.status == "permanently_failed"
        assert permanent.next_retry_at is None

    run(scenario())
