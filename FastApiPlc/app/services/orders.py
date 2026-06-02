from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.schemas import (
    ActiveOperation,
    Color,
    ErpEventType,
    IntegrationEventRead,
    LoadToHmiRequest,
    LoadedOrderRead,
    OrderColor,
    OrderStatus,
    RecipeRead,
)
from app.storage.json_store import JsonStore


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_actual_wpc(actual_wpc: int) -> Color:
    color_by_code = {1: Color.black, 2: Color.red, 3: Color.silver}
    color = color_by_code.get(actual_wpc)
    if color is None:
        raise ValueError("ActualWPC must be one of: 1 black, 2 red, 3 silver")
    return color


class OrderService:
    def __init__(self, store: JsonStore) -> None:
        self.store = store

    async def load_to_hmi(self, request: LoadToHmiRequest) -> LoadedOrderRead:
        created_at = now_iso()
        order_id = str(uuid4())
        recipes = {
            item.color.value: {
                "line_number": item.line_number,
                "color": item.color.value,
                "tags": item.tags,
                "cap": item.cap,
                "required": item.quantity,
                "started": 0,
                "completed": 0,
            }
            for item in request.items
        }
        order = {
            "id": order_id,
            "customer_order_number": request.customer_order_number,
            "customer_order_ref": request.customer_order_ref,
            "customer": request.customer,
            "status": OrderStatus.running.value,
            "created_at": created_at,
            "completed_at": None,
            "recipes": recipes,
            "active_operation": None,
            "rejected_count": 0,
            "completion_notified": False,
        }

        def mutator(data: dict[str, Any]) -> LoadedOrderRead:
            active = self._active_order_raw(data)
            if active and active["status"] == OrderStatus.running.value:
                raise ValueError("Another customer order is already running on the stand")
            data["orders"].append(order)
            return self._to_order_read(order)

        return await self.store.update(mutator)

    async def list_orders(self) -> list[LoadedOrderRead]:
        data = await self.store.load()
        return [self._to_order_read(order) for order in data["orders"]]

    async def get_order(self, order_id: str) -> LoadedOrderRead | None:
        data = await self.store.load()
        for order in data["orders"]:
            if order["id"] == order_id:
                return self._to_order_read(order)
        return None

    async def active_order(self) -> LoadedOrderRead | None:
        data = await self.store.load()
        order = self._active_order_raw(data)
        return self._to_order_read(order) if order else None

    async def handle_color_detected(self, actual_wpc: int) -> tuple[ActiveOperation, dict[str, Any] | None]:
        color = parse_actual_wpc(actual_wpc)
        created_at = now_iso()

        def mutator(data: dict[str, Any]) -> tuple[ActiveOperation, dict[str, Any] | None]:
            order = self._active_order_raw(data)
            if not order:
                return self._reject_operation(actual_wpc, color, created_at, "no_active_order"), None
            if order.get("active_operation"):
                raise ValueError("Previous container is still active")

            recipe = order["recipes"].get(color.value)
            if color == Color.silver or not recipe:
                operation = self._reject_operation(actual_wpc, color, created_at, "color_not_in_customer_order")
                order["active_operation"] = operation.model_dump(mode="json")
                return operation, None

            if recipe["started"] >= recipe["required"]:
                operation = self._reject_operation(actual_wpc, color, created_at, "color_quantity_already_completed")
                order["active_operation"] = operation.model_dump(mode="json")
                return operation, None

            unit_sequence = sum(item["started"] for item in order["recipes"].values()) + 1
            recipe["started"] += 1
            operation = ActiveOperation(
                id=str(uuid4()),
                decision="accepted",
                actual_wpc=actual_wpc,
                color=color,
                created_at=datetime.fromisoformat(created_at),
                unit_sequence=unit_sequence,
                tags=recipe["tags"],
                cap=recipe["cap"],
            )
            order["active_operation"] = operation.model_dump(mode="json")
            payload = {
                "event_id": f"EVT-{uuid4()}",
                "fastapi_order_id": order["id"],
                "customer_order_number": order["customer_order_number"],
                "color": color.value,
                "tags": recipe["tags"],
                "cap": recipe["cap"],
                "unit_sequence": unit_sequence,
            }
            return operation, payload

        return await self.store.update(mutator)

    async def handle_finished_tray(self) -> dict[str, Any] | None:
        finished_at = now_iso()

        def mutator(data: dict[str, Any]) -> dict[str, Any] | None:
            order = self._active_order_raw(data)
            if not order or not order.get("active_operation"):
                return None

            operation = order["active_operation"]
            if operation["decision"] != "accepted":
                return None

            recipe = order["recipes"][operation["color"]]
            recipe["completed"] += 1
            order["active_operation"] = None

            if self._order_totals(order)["completed"] >= self._order_totals(order)["required"]:
                order["status"] = OrderStatus.completed.value
                order["completed_at"] = finished_at
                if not order.get("completion_notified"):
                    order["completion_notified"] = True
                    return {
                        "event_id": f"EVT-{uuid4()}",
                        "fastapi_order_id": order["id"],
                        "customer_order_number": order["customer_order_number"],
                        "completed": {
                            color: recipe["completed"] for color, recipe in order["recipes"].items()
                        },
                        "rejected_count": order["rejected_count"],
                    }
            return None

        return await self.store.update(mutator)

    async def handle_reject_tray(self) -> None:
        completed_at = now_iso()

        def mutator(data: dict[str, Any]) -> None:
            order = self._active_order_raw(data)
            if order and order.get("active_operation") and order["active_operation"]["decision"] == "rejected":
                operation = order["active_operation"]
                order["rejected_count"] += 1
                order["active_operation"] = None
                data["rejects"].append(
                    {
                        "id": operation["id"],
                        "actual_wpc": operation["actual_wpc"],
                        "color": operation["color"],
                        "reject_reason": operation.get("reject_reason"),
                        "created_at": operation["created_at"],
                        "completed_at": completed_at,
                    }
                )

        await self.store.update(mutator)

    async def create_integration_event(self, event_type: ErpEventType, payload: dict[str, Any]) -> IntegrationEventRead:
        event = {
            "id": str(uuid4()),
            "event_type": event_type.value,
            "payload": payload,
            "status": "pending",
            "created_at": now_iso(),
            "sent_at": None,
            "error": None,
        }

        def mutator(data: dict[str, Any]) -> IntegrationEventRead:
            data["integration_events"].append(event)
            return self._to_integration_event(event)

        return await self.store.update(mutator)

    async def mark_integration_event(self, event_id: str, status: str, error: str | None = None) -> None:
        sent_at = now_iso() if status in {"sent", "skipped"} else None

        def mutator(data: dict[str, Any]) -> None:
            for event in data["integration_events"]:
                if event["id"] == event_id:
                    event["status"] = status
                    event["sent_at"] = sent_at
                    event["error"] = error
                    break

        await self.store.update(mutator)

    async def list_integration_events(self) -> list[IntegrationEventRead]:
        data = await self.store.load()
        return [self._to_integration_event(event) for event in data["integration_events"]]

    def _reject_operation(self, actual_wpc: int, color: Color, created_at: str, reason: str) -> ActiveOperation:
        return ActiveOperation(
            id=str(uuid4()),
            decision="rejected",
            actual_wpc=actual_wpc,
            color=color,
            created_at=datetime.fromisoformat(created_at),
            reject_reason=reason,
        )

    def _active_order_raw(self, data: dict[str, Any]) -> dict[str, Any] | None:
        for order in reversed(data["orders"]):
            if order["status"] == OrderStatus.running.value:
                return order
        return None

    def _order_totals(self, order: dict[str, Any]) -> dict[str, int]:
        recipes = order["recipes"].values()
        return {
            "required": sum(recipe["required"] for recipe in recipes),
            "started": sum(recipe["started"] for recipe in order["recipes"].values()),
            "completed": sum(recipe["completed"] for recipe in order["recipes"].values()),
        }

    def _to_order_read(self, order: dict[str, Any]) -> LoadedOrderRead:
        totals = self._order_totals(order)
        recipes = {
            OrderColor(color): RecipeRead(**recipe) for color, recipe in order["recipes"].items()
        }
        active_operation = order.get("active_operation")
        return LoadedOrderRead(
            id=order["id"],
            customer_order_number=order["customer_order_number"],
            customer_order_ref=order.get("customer_order_ref"),
            customer=order["customer"],
            status=OrderStatus(order["status"]),
            created_at=datetime.fromisoformat(order["created_at"]),
            completed_at=datetime.fromisoformat(order["completed_at"]) if order.get("completed_at") else None,
            recipes=recipes,
            total_required=totals["required"],
            total_started=totals["started"],
            total_completed=totals["completed"],
            rejected_count=order["rejected_count"],
            active_operation=ActiveOperation(**active_operation) if active_operation else None,
            completion_notified=order.get("completion_notified", False),
        )

    def _to_integration_event(self, event: dict[str, Any]) -> IntegrationEventRead:
        return IntegrationEventRead(
            id=event["id"],
            event_type=ErpEventType(event["event_type"]),
            payload=event["payload"],
            status=event["status"],
            created_at=datetime.fromisoformat(event["created_at"]),
            sent_at=datetime.fromisoformat(event["sent_at"]) if event.get("sent_at") else None,
            error=event.get("error"),
        )
