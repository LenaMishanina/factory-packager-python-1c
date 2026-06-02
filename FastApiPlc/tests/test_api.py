from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import ErpEventType
from app.storage.json_store import JsonStore


class RecordingErpClient:
    def __init__(self) -> None:
        self.events: list[tuple[ErpEventType, dict[str, Any]]] = []

    async def send_event(self, event_type: ErpEventType, payload: dict[str, Any]) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return {"status": "ok"}


def make_client(tmp_path: Path, erp_client: RecordingErpClient | None = None) -> TestClient:
    app = create_app(tmp_path / "orders.json", erp_client=erp_client or RecordingErpClient())
    return TestClient(app)


def load_sample_order(client: TestClient) -> str:
    response = client.post(
        "/orders/load-to-hmi",
        json={
            "customer_order_number": "ЗК-0001",
            "customer_order_ref": "e1cib/data/Документ.ЗаказКлиента?ref=test",
            "customer": "Учебный клиент",
            "items": [
                {"line_number": 1, "color": "black", "tags": 1, "cap": False, "quantity": 2},
                {"line_number": 2, "color": "red", "tags": 2, "cap": True, "quantity": 1},
            ],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["order"]["total_required"] == 3
    return body["data"]["order"]["id"]


def test_load_order_writes_recipes_to_hmi_and_starts_stand(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    load_sample_order(client)

    state = client.get("/plc/state").json()["data"]
    assert state["hmi"]["StartButton"] is True
    assert state["hmi"]["BlackTags"] == 1
    assert state["hmi"]["BlackCap"] is False
    assert state["hmi"]["RedTags"] == 2
    assert state["hmi"]["RedCap"] is True
    assert state["hmi"]["SilverTags"] == 0
    assert state["hmi"]["SilverCap"] is False


def test_invalid_order_constraints_are_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    silver = client.post(
        "/orders/load-to-hmi",
        json={
            "customer_order_number": "ЗК-0002",
            "items": [{"line_number": 1, "color": "silver", "tags": 1, "cap": False, "quantity": 1}],
        },
    )
    assert silver.status_code == 422

    too_many_tags = client.post(
        "/orders/load-to-hmi",
        json={
            "customer_order_number": "ЗК-0003",
            "items": [{"line_number": 1, "color": "black", "tags": 4, "cap": False, "quantity": 1}],
        },
    )
    assert too_many_tags.status_code == 422

    duplicate_color = client.post(
        "/orders/load-to-hmi",
        json={
            "customer_order_number": "ЗК-0004",
            "items": [
                {"line_number": 1, "color": "black", "tags": 1, "cap": False, "quantity": 1},
                {"line_number": 2, "color": "black", "tags": 2, "cap": True, "quantity": 1},
            ],
        },
    )
    assert duplicate_color.status_code == 422


def test_color_counter_accepts_matching_container_and_notifies_erp(tmp_path: Path) -> None:
    erp = RecordingErpClient()
    client = make_client(tmp_path, erp)
    load_sample_order(client)

    response = client.post("/plc/simulate/color-detected", json={"actual_wpc": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["operation"]["decision"] == "accepted"
    assert body["data"]["operation"]["color"] == "black"
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide1_Control"] is True
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide2_Control"] is False

    assert len(erp.events) == 1
    event_type, payload = erp.events[0]
    assert event_type == ErpEventType.production_unit_start
    assert payload["customer_order_number"] == "ЗК-0001"
    assert payload["color"] == "black"
    assert payload["tags"] == 1
    assert payload["cap"] is False
    assert payload["unit_sequence"] == 1


def test_unneeded_color_goes_to_reject_without_erp_event(tmp_path: Path) -> None:
    erp = RecordingErpClient()
    client = make_client(tmp_path, erp)
    client.post(
        "/orders/load-to-hmi",
        json={
            "customer_order_number": "ЗК-0005",
            "items": [{"line_number": 1, "color": "black", "tags": 1, "cap": False, "quantity": 2}],
        },
    )

    response = client.post("/plc/simulate/color-detected", json={"actual_wpc": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["operation"]["decision"] == "rejected"
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide1_Control"] is False
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide2_Control"] is True
    assert erp.events == []

    client.post("/plc/simulate/reject-tray", json={})
    order = client.get("/orders/active").json()["data"]["order"]
    assert order["rejected_count"] == 1


def test_full_physical_flow_stops_stand_and_sends_completion(tmp_path: Path) -> None:
    erp = RecordingErpClient()
    client = make_client(tmp_path, erp)
    order_id = client.post(
        "/orders/load-to-hmi",
        json={
            "customer_order_number": "ЗК-0006",
            "items": [{"line_number": 1, "color": "black", "tags": 1, "cap": False, "quantity": 2}],
        },
    ).json()["data"]["order"]["id"]

    client.post("/plc/simulate/color-detected", json={"actual_wpc": 1})
    client.post("/plc/simulate/finished-tray", json={})
    client.post("/plc/simulate/color-detected", json={"actual_wpc": 1})
    client.post("/plc/simulate/finished-tray", json={})

    order = client.get(f"/orders/{order_id}").json()["data"]["order"]
    state = client.get("/plc/state").json()["data"]
    assert order["status"] == "completed"
    assert order["total_completed"] == 2
    assert state["hmi"]["StartButton"] is False

    assert [event[0] for event in erp.events] == [
        ErpEventType.production_unit_start,
        ErpEventType.production_unit_start,
        ErpEventType.customer_order_complete,
    ]
    assert erp.events[-1][1]["completed"] == {"black": 2}


def test_json_store_persists_loaded_orders(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    load_sample_order(client)

    store = JsonStore(tmp_path / "orders.json")
    data = TestClient(create_app(tmp_path / "orders.json")).get("/orders").json()["data"]
    assert len(data["orders"]) == 1
    assert store.path.exists()
