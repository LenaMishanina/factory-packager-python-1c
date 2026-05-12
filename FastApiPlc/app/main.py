from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.schemas import (
    AVAILABLE_TAGS,
    ActualWpcRequest,
    ApiResponse,
    CompleteRequest,
    OrderCreateRequest,
)
from app.services.orders import OrderService, parse_actual_wpc, tags_warning
from app.services.plc_stub import PlcStub
from app.storage.json_store import JsonStore


ROOT = Path(__file__).resolve().parent.parent


tags_metadata = [
    {
        "name": "System",
        "description": "Служебные методы: проверка запуска и healthcheck.",
    },
    {
        "name": "Orders",
        "description": "Работа с документами 1C «Заказ клиента».",
    },
    {
        "name": "PLC simulator",
        "description": "REST-заглушка стенда до подключения Snap7/TIA Portal.",
    },
]


def create_app(storage_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Factory Packager PLC API",
        description=(
            "FastAPI-сервис для интеграции 1C:ERP с учебным стендом упаковки. "
            "Текущая версия использует JSON-хранилище и REST-симулятор PLC."
        ),
        version="0.1.0",
        openapi_tags=tags_metadata,
    )

    store = JsonStore(storage_path or ROOT / "data" / "orders.json")
    app.state.order_service = OrderService(store)
    app.state.plc = PlcStub()

    @app.get(
        "/",
        tags=["System"],
        response_model=ApiResponse,
        summary="Проверить запуск API",
        description="Returns a simple response to confirm that the FastAPI service is running.",
    )
    async def root() -> ApiResponse:
        return ApiResponse(success=True, message="FastAPI-сервис упаковщика запущен")

    @app.get(
        "/health",
        tags=["System"],
        response_model=ApiResponse,
        summary="Healthcheck сервера",
        description="Health endpoint for Postman, 1C, or monitoring checks.",
    )
    async def health() -> ApiResponse:
        return ApiResponse(success=True, message="Сервер доступен", data={"status": "ok"})

    @app.post(
        "/orders",
        tags=["Orders"],
        response_model=ApiResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Добавить заказ клиента",
        description="1C sends a full customer order. The server expands quantities into a FIFO item queue.",
    )
    async def create_order(payload: OrderCreateRequest, request: Request) -> ApiResponse:
        service: OrderService = request.app.state.order_service
        order = await service.create_order(payload)
        warnings = [
            warning
            for item in payload.items
            if (warning := tags_warning(item.tags)) is not None
        ]
        return ApiResponse(
            success=True,
            message="Заказ клиента был добавлен",
            data={
                "order": order,
                "available_tags": AVAILABLE_TAGS,
                "warnings": warnings,
            },
        )

    @app.get(
        "/orders",
        tags=["Orders"],
        response_model=ApiResponse,
        summary="Получить список заказов",
        description="Returns all customer orders with calculated statuses and item counters.",
    )
    async def list_orders(request: Request) -> ApiResponse:
        service: OrderService = request.app.state.order_service
        return ApiResponse(success=True, message="Список заказов получен", data={"orders": await service.list_orders()})

    @app.get(
        "/orders/{order_id}",
        tags=["Orders"],
        response_model=ApiResponse,
        summary="Получить заказ по ID",
        description="Returns one customer order with all expanded package items.",
    )
    async def get_order(order_id: str, request: Request) -> ApiResponse | JSONResponse:
        service: OrderService = request.app.state.order_service
        order = await service.get_order(order_id)
        if order is None:
            return error_response(status.HTTP_404_NOT_FOUND, "Заказ клиента не найден")
        return ApiResponse(success=True, message="Заказ клиента найден", data={"order": order})

    @app.post(
        "/plc/simulate/current-wpc",
        tags=["PLC simulator"],
        response_model=ApiResponse,
        summary="Передать текущую емкость ActualWPC",
        description=(
            "Simulates PLC sending ActualWPC. The server chooses the first matching pending order item "
            "or routes the package to reject."
        ),
    )
    async def simulate_current_wpc(payload: ActualWpcRequest, request: Request) -> ApiResponse | JSONResponse:
        service: OrderService = request.app.state.order_service
        plc: PlcStub = request.app.state.plc

        if plc.current_operation and plc.current_operation.completed_at is None:
            return error_response(
                status.HTTP_409_CONFLICT,
                "Предыдущая емкость еще не завершила процесс производства",
                {"current_operation": plc.current_operation},
            )

        try:
            wpc_info = parse_actual_wpc(payload.actual_wpc)
        except ValueError as exc:
            return error_response(status.HTTP_400_BAD_REQUEST, str(exc))

        operation = await service.reserve_next_for_wpc(wpc_info)
        order = await service.get_order(operation.order_id) if operation.order_id else None
        item = None
        if order and operation.item_id:
            item = next((candidate for candidate in order.items if candidate.id == operation.item_id), None)

        plc.apply_operation(operation, tags=item.tags if item else None, cap=item.cap if item else None)

        if item:
            message = "Подходящая емкость найдена, изделие записано в Hmi и отправлено в готовую продукцию"
        else:
            message = "Подходящего изделия в заказах нет, емкость отправлена в брак"

        return ApiResponse(
            success=True,
            message=message,
            data={
                "operation": operation,
                "matched_item": item,
                "plc_state": await plc_state(service, plc),
            },
        )

    @app.post(
        "/plc/simulate/complete",
        tags=["PLC simulator"],
        response_model=ApiResponse,
        summary="Завершить текущую емкость",
        description="Simulates counterPIP and marks the current accepted or rejected package as completed.",
    )
    async def simulate_complete(payload: CompleteRequest, request: Request) -> ApiResponse | JSONResponse:
        service: OrderService = request.app.state.order_service
        plc: PlcStub = request.app.state.plc

        if not payload.counter_pip:
            return error_response(status.HTTP_400_BAD_REQUEST, "counter_pip=false: емкость еще не дошла до лотка")
        if plc.current_operation is None:
            return error_response(status.HTTP_409_CONFLICT, "Нет текущей емкости для завершения")
        if plc.current_operation.completed_at is not None:
            return error_response(status.HTTP_409_CONFLICT, "Текущая емкость уже была завершена")

        completed = await service.complete_operation(plc.current_operation)
        plc.complete_current(completed)
        plc.clear_completed_operation()
        return ApiResponse(
            success=True,
            message="Процесс производства одной емкости завершен",
            data={"operation": completed, "plc_state": await plc_state(service, plc)},
        )

    @app.get(
        "/plc/state",
        tags=["PLC simulator"],
        response_model=ApiResponse,
        summary="Получить состояние PLC-заглушки",
        description="Returns current Hmi and dbMyData values kept by the REST simulator.",
    )
    async def get_plc_state(request: Request) -> ApiResponse:
        service: OrderService = request.app.state.order_service
        plc: PlcStub = request.app.state.plc
        return ApiResponse(success=True, message="Состояние PLC-заглушки получено", data=await plc_state(service, plc))

    @app.post(
        "/plc/reset",
        tags=["PLC simulator"],
        response_model=ApiResponse,
        summary="Имитировать калибровку стенда",
        description="Sets ResetButton to True and then False, marking the stand as calibrated.",
    )
    async def reset_plc(request: Request) -> ApiResponse:
        plc: PlcStub = request.app.state.plc
        service: OrderService = request.app.state.order_service
        plc.reset()
        return ApiResponse(success=True, message="Калибровка стенда выполнена", data=await plc_state(service, plc))

    @app.post(
        "/plc/start",
        tags=["PLC simulator"],
        response_model=ApiResponse,
        summary="Запустить стенд",
        description="Sets Hmi.StartButton=True. While True, the factory continues receiving packages.",
    )
    async def start_plc(request: Request) -> ApiResponse:
        plc: PlcStub = request.app.state.plc
        service: OrderService = request.app.state.order_service
        plc.start()
        return ApiResponse(success=True, message="Стенд запущен", data=await plc_state(service, plc))

    @app.post(
        "/plc/stop",
        tags=["PLC simulator"],
        response_model=ApiResponse,
        summary="Остановить стенд",
        description="Sets Hmi.StartButton=False. New packages should stop entering the conveyor line.",
    )
    async def stop_plc(request: Request) -> ApiResponse:
        plc: PlcStub = request.app.state.plc
        service: OrderService = request.app.state.order_service
        plc.stop()
        return ApiResponse(success=True, message="Стенд остановлен", data=await plc_state(service, plc))

    return app


async def plc_state(service: OrderService, plc: PlcStub) -> dict:
    return plc.state(rejected_count=await service.rejected_count()).model_dump(mode="json")


def error_response(status_code: int, message: str, data: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiResponse(success=False, message=message, data=data).model_dump(mode="json"),
    )


app = create_app()
