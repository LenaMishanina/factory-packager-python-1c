from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.schemas import (
    AVAILABLE_TAGS,
    ActualWpcInfo,
    Color,
    Decision,
    OperationState,
    OrderCreateRequest,
    OrderItemStatus,
    OrderRead,
    OrderStatus,
)
from app.storage.json_store import JsonStore


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_actual_wpc(actual_wpc: int) -> ActualWpcInfo:
    cap_detected = actual_wpc >= 10
    color_code = actual_wpc - 10 if cap_detected else actual_wpc
    color_by_code = {1: Color.black, 2: Color.red, 3: Color.silver}
    color = color_by_code.get(color_code)
    if color is None:
        raise ValueError("ActualWPC must be one of: 1, 2, 3, 11, 12, 13")
    return ActualWpcInfo(actual_wpc=actual_wpc, color=color, cap_detected=cap_detected)


class OrderService:
    def __init__(self, store: JsonStore) -> None:
        self.store = store

    async def create_order(self, request: OrderCreateRequest) -> OrderRead:
        created_at = now_iso()
        order_id = str(uuid4())
        units: list[dict[str, Any]] = []
        sequence = 1

        for line_index, product in enumerate(request.items, start=1):
            for _ in range(product.quantity):
                units.append(
                    {
                        "id": str(uuid4()),
                        "source_line": line_index,
                        "sequence": sequence,
                        "color": product.color.value,
                        "tags": product.tags,
                        "cap": product.cap,
                        "status": OrderItemStatus.pending.value,
                        "started_at": None,
                        "completed_at": None,
                    }
                )
                sequence += 1

        order = {
            "id": order_id,
            "document_number": request.document_number,
            "created_at": created_at,
            "items": units,
        }

        def mutator(data: dict[str, Any]) -> OrderRead:
            data["orders"].append(order)
            return self._to_order_read(order)

        return await self.store.update(mutator)

    async def list_orders(self) -> list[OrderRead]:
        data = await self.store.load()
        return [self._to_order_read(order) for order in data["orders"]]

    async def get_order(self, order_id: str) -> OrderRead | None:
        data = await self.store.load()
        for order in data["orders"]:
            if order["id"] == order_id:
                return self._to_order_read(order)
        return None

    async def reserve_next_for_wpc(self, info: ActualWpcInfo) -> OperationState:
        created_at = now_iso()

        def mutator(data: dict[str, Any]) -> OperationState:
            for order in data["orders"]:
                for item in order["items"]:
                    if item["status"] == OrderItemStatus.pending.value and item["color"] == info.color.value:
                        item["status"] = OrderItemStatus.processing.value
                        item["started_at"] = created_at
                        return OperationState(
                            id=str(uuid4()),
                            decision=Decision.accepted,
                            actual_wpc=info.actual_wpc,
                            color=info.color,
                            cap_detected=info.cap_detected,
                            order_id=order["id"],
                            item_id=item["id"],
                            created_at=datetime.fromisoformat(created_at),
                        )

            reject = {
                "id": str(uuid4()),
                "actual_wpc": info.actual_wpc,
                "color": info.color.value,
                "cap_detected": info.cap_detected,
                "created_at": created_at,
                "completed_at": None,
            }
            data["rejects"].append(reject)
            return OperationState(
                id=reject["id"],
                decision=Decision.rejected,
                actual_wpc=info.actual_wpc,
                color=info.color,
                cap_detected=info.cap_detected,
                created_at=datetime.fromisoformat(created_at),
            )

        return await self.store.update(mutator)

    async def complete_operation(self, operation: OperationState) -> OperationState:
        completed_at = now_iso()

        def mutator(data: dict[str, Any]) -> OperationState:
            if operation.decision == Decision.accepted and operation.order_id and operation.item_id:
                for order in data["orders"]:
                    if order["id"] != operation.order_id:
                        continue
                    for item in order["items"]:
                        if item["id"] == operation.item_id:
                            item["status"] = OrderItemStatus.completed.value
                            item["completed_at"] = completed_at
                            break
            else:
                for reject in data["rejects"]:
                    if reject["id"] == operation.id:
                        reject["completed_at"] = completed_at
                        break

            return operation.model_copy(update={"completed_at": datetime.fromisoformat(completed_at)})

        return await self.store.update(mutator)

    async def rejected_count(self) -> int:
        data = await self.store.load()
        return len(data["rejects"])

    def _to_order_read(self, order: dict[str, Any]) -> OrderRead:
        items = order["items"]
        total = len(items)
        pending = sum(1 for item in items if item["status"] == OrderItemStatus.pending.value)
        processing = sum(1 for item in items if item["status"] == OrderItemStatus.processing.value)
        completed = sum(1 for item in items if item["status"] == OrderItemStatus.completed.value)

        if completed == total:
            status = OrderStatus.completed
        elif completed or processing:
            status = OrderStatus.in_progress
        else:
            status = OrderStatus.pending

        return OrderRead(
            id=order["id"],
            document_number=order["document_number"],
            status=status,
            created_at=datetime.fromisoformat(order["created_at"]),
            total_items=total,
            pending_items=pending,
            processing_items=processing,
            completed_items=completed,
            items=items,
        )


def tags_warning(tags: int) -> str | None:
    if tags > AVAILABLE_TAGS:
        return f"Запрошено {tags} фишек, физически сейчас доступно {AVAILABLE_TAGS}."
    return None
