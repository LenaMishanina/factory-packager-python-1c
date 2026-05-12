from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage.json_store import JsonStore


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "orders.json")
    return TestClient(app)


def create_sample_order(client: TestClient) -> str:
    response = client.post(
        "/orders",
        json={
            "document_number": "1C-0001",
            "items": [
                {"color": "черная", "tags": 3, "cap": True, "quantity": 1},
                {"color": "red", "tags": 7, "cap": False, "quantity": 2},
            ],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert "Заказ клиента" in body["message"]
    assert body["data"]["order"]["total_items"] == 3
    assert body["data"]["warnings"] == ["Запрошено 7 фишек, физически сейчас доступно 5."]
    return body["data"]["order"]["id"]


def test_create_order_and_common_response_shape(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    order_id = create_sample_order(client)

    response = client.get(f"/orders/{order_id}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"success", "message", "data"}
    assert body["data"]["order"]["pending_items"] == 3


def test_current_wpc_selects_first_matching_item(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    create_sample_order(client)

    response = client.post("/plc/simulate/current-wpc", json={"actual_wpc": 12})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["operation"]["decision"] == "accepted"
    assert body["data"]["matched_item"]["color"] == "red"
    assert body["data"]["plc_state"]["hmi"]["RedTags"] == 7
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide1_Control"] is True
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide2_Control"] is False


def test_unmatched_wpc_goes_to_reject(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post(
        "/orders",
        json={
            "document_number": "1C-0002",
            "items": [{"color": "black", "tags": 1, "cap": False, "quantity": 1}],
        },
    )

    response = client.post("/plc/simulate/current-wpc", json={"actual_wpc": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["operation"]["decision"] == "rejected"
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide1_Control"] is False
    assert body["data"]["plc_state"]["db_my_data"]["WpcSlide2_Control"] is True
    assert body["data"]["plc_state"]["rejected_count"] == 1


def test_complete_marks_item_completed(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    order_id = create_sample_order(client)
    client.post("/plc/simulate/current-wpc", json={"actual_wpc": 1})

    response = client.post("/plc/simulate/complete", json={"counter_pip": True})
    assert response.status_code == 200
    assert response.json()["success"] is True

    order = client.get(f"/orders/{order_id}").json()["data"]["order"]
    assert order["completed_items"] == 1
    assert order["pending_items"] == 2
    assert order["items"][0]["status"] == "completed"


def test_json_store_persists_orders(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    create_sample_order(client)

    store = JsonStore(tmp_path / "orders.json")
    data = TestClient(create_app(tmp_path / "orders.json")).get("/orders").json()["data"]
    assert len(data["orders"]) == 1
    assert store.path.exists()
