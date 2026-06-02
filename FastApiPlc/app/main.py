from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.schemas import (
    ActualWpcRequest,
    ApiResponse,
    CompleteRequest,
    ErpEventType,
    EventCounterRequest,
    LoadToHmiRequest,
)
from app.services.erp_client import ErpClient
from app.services.orders import OrderService
from app.services.plc_client import Snap7PlcClient
from app.services.plc_stub import PlcStub
from app.storage.json_store import JsonStore


ROOT = Path(__file__).resolve().parent.parent


class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


tags_metadata = [
    {"name": "System", "description": "Служебные методы и healthcheck."},
    {"name": "Orders", "description": "Заказы клиента и план производства для стенда."},
    {"name": "PLC", "description": "Состояние, запуск и события SIMATIC/TIA Portal."},
    {"name": "Integration", "description": "Журнал исходящих событий в HTTP-сервис 1C:ERP."},
]


def create_app(
    storage_path: str | Path | None = None,
    *,
    plc: Any | None = None,
    erp_client: ErpClient | None = None,
    start_polling: bool | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sync_counter_baseline(app)
        if app.state.start_polling:
            app.state.polling_task = asyncio.create_task(plc_polling_loop(app))
        try:
            yield
        finally:
            task = app.state.polling_task
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="Factory Packager PLC API",
        description="FastAPI-сервис интеграции 1C:ERP с учебным стендом упаковки SIMATIC.",
        version="0.2.0",
        openapi_tags=tags_metadata,
        lifespan=lifespan,
        default_response_class=Utf8JSONResponse,
    )

    store = JsonStore(storage_path or ROOT / "data" / "orders.json")
    app.state.order_service = OrderService(store)
    app.state.erp_client = erp_client or ErpClient(os.getenv("ERP_BASE_URL"))
    app.state.plc = plc or create_plc_from_env()
    app.state.last_color_counter = 0
    app.state.last_finished_counter = 0
    app.state.last_reject_counter = 0
    app.state.polling_task = None
    app.state.start_polling = (
        start_polling if start_polling is not None else os.getenv("PLC_POLLING_ENABLED", "false").lower() == "true"
    )

    @app.get("/", tags=["System"], response_model=ApiResponse, summary="Проверить запуск API")
    async def root() -> ApiResponse:
        return ApiResponse(success=True, message="FastAPI-сервис упаковщика запущен")

    @app.get("/health", tags=["System"], response_model=ApiResponse, summary="Healthcheck сервера")
    async def health() -> ApiResponse:
        return ApiResponse(success=True, message="Сервер доступен", data={"status": "ok"})

    @app.post(
        "/orders/load-to-hmi",
        tags=["Orders"],
        response_model=ApiResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Загрузить заказ клиента в Hmi и запустить стенд",
    )
    async def load_order_to_hmi(payload: LoadToHmiRequest, request: Request) -> ApiResponse | JSONResponse:
        service: OrderService = request.app.state.order_service
        plc_client = request.app.state.plc
        try:
            order = await service.load_to_hmi(payload)
        except ValueError as exc:
            return error_response(status.HTTP_409_CONFLICT, str(exc))

        plc_client.load_recipes(order.recipes)
        plc_client.reset()
        plc_client.start()
        sync_counter_baseline(request.app)
        return ApiResponse(
            success=True,
            message="Заказ клиента загружен в Hmi, стенд откалиброван и запущен",
            data={"order": order, "plc_state": plc_client.state()},
        )

    @app.get("/orders", tags=["Orders"], response_model=ApiResponse, summary="Получить список заказов")
    async def list_orders(request: Request) -> ApiResponse:
        service: OrderService = request.app.state.order_service
        return ApiResponse(success=True, message="Список заказов получен", data={"orders": await service.list_orders()})

    @app.get("/orders/active", tags=["Orders"], response_model=ApiResponse, summary="Получить активный заказ")
    async def get_active_order(request: Request) -> ApiResponse:
        service: OrderService = request.app.state.order_service
        return ApiResponse(success=True, message="Активный заказ получен", data={"order": await service.active_order()})

    @app.get("/orders/{order_id}", tags=["Orders"], response_model=ApiResponse, summary="Получить заказ по ID")
    async def get_order(order_id: str, request: Request) -> ApiResponse | JSONResponse:
        service: OrderService = request.app.state.order_service
        order = await service.get_order(order_id)
        if order is None:
            return error_response(status.HTTP_404_NOT_FOUND, "Заказ клиента не найден")
        return ApiResponse(success=True, message="Заказ клиента найден", data={"order": order})

    @app.get("/plc/state", tags=["PLC"], response_model=ApiResponse, summary="Получить состояние ПЛК")
    async def get_plc_state(request: Request) -> ApiResponse:
        return ApiResponse(success=True, message="Состояние ПЛК получено", data=request.app.state.plc.state())

    @app.post("/plc/reset", tags=["PLC"], response_model=ApiResponse, summary="Калибровка стенда")
    async def reset_plc(request: Request) -> ApiResponse:
        request.app.state.plc.reset()
        return ApiResponse(success=True, message="Калибровка стенда выполнена", data=request.app.state.plc.state())

    @app.post("/plc/start", tags=["PLC"], response_model=ApiResponse, summary="Запустить стенд")
    async def start_plc(request: Request) -> ApiResponse:
        request.app.state.plc.start()
        return ApiResponse(success=True, message="Стенд запущен", data=request.app.state.plc.state())

    @app.post("/plc/stop", tags=["PLC"], response_model=ApiResponse, summary="Остановить стенд")
    async def stop_plc(request: Request) -> ApiResponse:
        request.app.state.plc.stop()
        return ApiResponse(success=True, message="Стенд остановлен", data=request.app.state.plc.state())

    @app.post(
        "/plc/simulate/color-detected",
        tags=["PLC"],
        response_model=ApiResponse,
        summary="Симулировать определение цвета емкости",
    )
    async def simulate_color_detected(payload: ActualWpcRequest, request: Request) -> ApiResponse | JSONResponse:
        plc_client = request.app.state.plc
        if not hasattr(plc_client, "simulate_color_detected"):
            return error_response(status.HTTP_400_BAD_REQUEST, "Симуляция доступна только для PlcStub")
        plc_client.simulate_color_detected(payload.actual_wpc)
        request.app.state.last_color_counter = plc_client.state().hmi.ColorDetectedCounter
        return await process_color_detected(request.app, payload.actual_wpc)

    @app.post(
        "/plc/simulate/finished-tray",
        tags=["PLC"],
        response_model=ApiResponse,
        summary="Симулировать попадание изделия в лоток готовой продукции",
    )
    async def simulate_finished_tray(payload: EventCounterRequest, request: Request) -> ApiResponse | JSONResponse:
        plc_client = request.app.state.plc
        if not hasattr(plc_client, "simulate_finished_tray"):
            return error_response(status.HTTP_400_BAD_REQUEST, "Симуляция доступна только для PlcStub")
        plc_client.simulate_finished_tray()
        if payload.value is not None:
            plc_client.hmi.FinishedTrayCounter = payload.value
        request.app.state.last_finished_counter = plc_client.state().hmi.FinishedTrayCounter
        return await process_finished_tray(request.app)

    @app.post(
        "/plc/simulate/reject-tray",
        tags=["PLC"],
        response_model=ApiResponse,
        summary="Симулировать попадание емкости в лоток брака",
    )
    async def simulate_reject_tray(payload: EventCounterRequest, request: Request) -> ApiResponse:
        plc_client = request.app.state.plc
        if hasattr(plc_client, "simulate_reject_tray"):
            plc_client.simulate_reject_tray()
            if payload.value is not None:
                plc_client.hmi.RejectTrayCounter = payload.value
            request.app.state.last_reject_counter = plc_client.state().hmi.RejectTrayCounter
        await process_reject_tray(request.app)
        return ApiResponse(success=True, message="Бракованная емкость зафиксирована", data={"plc_state": plc_client.state()})

    # Compatibility endpoints kept for old Postman/tests while the new counter model is adopted.
    @app.post("/plc/simulate/current-wpc", tags=["PLC"], response_model=ApiResponse, include_in_schema=False)
    async def simulate_current_wpc(payload: ActualWpcRequest, request: Request) -> ApiResponse | JSONResponse:
        return await simulate_color_detected(payload, request)

    @app.post("/plc/simulate/complete", tags=["PLC"], response_model=ApiResponse, include_in_schema=False)
    async def simulate_complete(payload: CompleteRequest, request: Request) -> ApiResponse | JSONResponse:
        if not payload.counter_pip:
            return error_response(status.HTTP_400_BAD_REQUEST, "counter_pip=false: емкость еще не дошла до лотка")
        return await simulate_finished_tray(EventCounterRequest(), request)

    @app.get(
        "/integration/events",
        tags=["Integration"],
        response_model=ApiResponse,
        summary="Получить журнал исходящих событий в 1C",
    )
    async def list_integration_events(request: Request) -> ApiResponse:
        service: OrderService = request.app.state.order_service
        return ApiResponse(
            success=True,
            message="Журнал интеграции получен",
            data={"events": await service.list_integration_events()},
        )

    return app


def create_plc_from_env() -> Any:
    if os.getenv("PLC_MODE", "stub").lower() != "real":
        return PlcStub()
    return Snap7PlcClient(
        host=os.getenv("PLC_HOST", "192.168.0.1"),
        rack=int(os.getenv("PLC_RACK", "0")),
        slot=int(os.getenv("PLC_SLOT", "1")),
    )


async def process_color_detected(app: FastAPI, actual_wpc: int) -> ApiResponse | JSONResponse:
    service: OrderService = app.state.order_service
    try:
        operation, payload = await service.handle_color_detected(actual_wpc)
    except ValueError as exc:
        return error_response(status.HTTP_409_CONFLICT, str(exc))

    app.state.plc.apply_operation(operation)
    integration_event = None
    if payload:
        integration_event = await dispatch_erp_event(app, ErpEventType.production_unit_start, payload)

    message = (
        "Подходящая емкость найдена, 1C уведомлена о запуске производства одной единицы"
        if operation.decision == "accepted"
        else "Емкость не подходит заказу и направлена в лоток брака"
    )
    return ApiResponse(
        success=True,
        message=message,
        data={"operation": operation, "integration_event": integration_event, "plc_state": app.state.plc.state()},
    )


async def process_finished_tray(app: FastAPI) -> ApiResponse:
    service: OrderService = app.state.order_service
    payload = await service.handle_finished_tray()
    if app.state.plc.current_operation and app.state.plc.current_operation.decision == "accepted":
        app.state.plc.clear_operation()

    integration_event = None
    if payload:
        app.state.plc.stop()
        integration_event = await dispatch_erp_event(app, ErpEventType.customer_order_complete, payload)

    return ApiResponse(
        success=True,
        message="Изделие в лотке готовой продукции зафиксировано",
        data={"completion_event": integration_event, "plc_state": app.state.plc.state()},
    )


async def process_reject_tray(app: FastAPI) -> None:
    await app.state.order_service.handle_reject_tray()
    if app.state.plc.current_operation and app.state.plc.current_operation.decision == "rejected":
        app.state.plc.clear_operation()


async def dispatch_erp_event(app: FastAPI, event_type: ErpEventType, payload: dict[str, Any]) -> dict[str, Any]:
    service: OrderService = app.state.order_service
    event = await service.create_integration_event(event_type, payload)
    try:
        result = await app.state.erp_client.send_event(event_type, payload)
    except Exception as exc:  # pragma: no cover - network behavior depends on the local ERP publication
        await service.mark_integration_event(event.id, "failed", str(exc))
        return {"id": event.id, "status": "failed", "error": str(exc)}

    status_value = "skipped" if result.get("status") == "skipped" else "sent"
    await service.mark_integration_event(event.id, status_value)
    return {"id": event.id, "status": status_value, "response": result}


async def plc_polling_loop(app: FastAPI) -> None:
    while True:
        state = app.state.plc.state()
        if state.hmi.ColorDetectedCounter != app.state.last_color_counter:
            app.state.last_color_counter = state.hmi.ColorDetectedCounter
            await process_color_detected(app, state.hmi.ActualWPC)
        if state.hmi.FinishedTrayCounter != app.state.last_finished_counter:
            app.state.last_finished_counter = state.hmi.FinishedTrayCounter
            await process_finished_tray(app)
        if state.hmi.RejectTrayCounter != app.state.last_reject_counter:
            app.state.last_reject_counter = state.hmi.RejectTrayCounter
            await process_reject_tray(app)
        await asyncio.sleep(0.5)


def sync_counter_baseline(app: FastAPI) -> None:
    state = app.state.plc.state()
    app.state.last_color_counter = state.hmi.ColorDetectedCounter
    app.state.last_finished_counter = state.hmi.FinishedTrayCounter
    app.state.last_reject_counter = state.hmi.RejectTrayCounter


def error_response(status_code: int, message: str, data: dict | None = None) -> JSONResponse:
    return Utf8JSONResponse(
        status_code=status_code,
        content=ApiResponse(success=False, message=message, data=data).model_dump(mode="json"),
    )


app = create_app()
