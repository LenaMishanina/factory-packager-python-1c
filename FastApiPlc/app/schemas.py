from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_TAGS = 3


class Color(str, Enum):
    black = "black"
    red = "red"
    silver = "silver"


class OrderColor(str, Enum):
    black = "black"
    red = "red"


COLOR_ALIASES = {
    "black": Color.black,
    "черная": Color.black,
    "чёрная": Color.black,
    "черный": Color.black,
    "чёрный": Color.black,
    "black емкость": Color.black,
    "red": Color.red,
    "красная": Color.red,
    "красный": Color.red,
    "red емкость": Color.red,
    "silver": Color.silver,
    "серебристая": Color.silver,
    "серебристый": Color.silver,
    "silver емкость": Color.silver,
}


def normalize_color(value: Any) -> Color:
    if isinstance(value, Color):
        return value
    if isinstance(value, OrderColor):
        return Color(value.value)
    if isinstance(value, str):
        key = value.strip().lower()
        if key in COLOR_ALIASES:
            return COLOR_ALIASES[key]
    raise ValueError("Color must be black/red/silver or черная/красная/серебристая")


def normalize_order_color(value: Any) -> OrderColor:
    color = normalize_color(value)
    if color == Color.silver:
        raise ValueError("Silver products are disabled for this stand scenario")
    return OrderColor(color.value)


class ApiResponse(BaseModel):
    success: bool = Field(..., examples=[True])
    message: str = Field(..., examples=["Операция выполнена успешно"])
    data: Any | None = None


class OrderLineRequest(BaseModel):
    line_number: int = Field(..., ge=1, description="Номер строки в заказе клиента")
    color: OrderColor = Field(..., description="Цвет изделия: black/red")
    tags: int = Field(0, ge=0, le=MAX_TAGS, description="Количество фишек от 0 до 3")
    cap: bool = Field(False, description="Нужно ли закрыть емкость крышкой")
    quantity: int = Field(1, ge=1, description="Количество изделий с данным рецептом")

    @field_validator("color", mode="before")
    @classmethod
    def validate_color(cls, value: Any) -> OrderColor:
        return normalize_order_color(value)


class LoadToHmiRequest(BaseModel):
    customer_order_number: str = Field(..., min_length=1, description="Номер документа «Заказ клиента»")
    customer_order_ref: str | None = Field(None, description="Ссылка 1C на «Заказ клиента»")
    customer: str = Field("Учебный клиент", min_length=1, description="Клиент заказа")
    items: list[OrderLineRequest] = Field(..., min_length=1, description="Позиции заказа")

    @model_validator(mode="after")
    def validate_single_recipe_per_color(self) -> LoadToHmiRequest:
        seen: set[OrderColor] = set()
        for item in self.items:
            if item.color in seen:
                raise ValueError("Only one recipe per color is allowed in one customer order")
            seen.add(item.color)
        return self


class RecipeRead(BaseModel):
    line_number: int
    color: OrderColor
    tags: int
    cap: bool
    required: int
    started: int
    completed: int


class OrderStatus(str, Enum):
    running = "running"
    completed = "completed"
    stopped = "stopped"


class ActiveOperation(BaseModel):
    id: str
    decision: str
    actual_wpc: int
    color: Color
    created_at: datetime
    unit_sequence: int | None = None
    tags: int | None = None
    cap: bool | None = None
    reject_reason: str | None = None


class LoadedOrderRead(BaseModel):
    id: str
    customer_order_number: str
    customer_order_ref: str | None = None
    customer: str
    status: OrderStatus
    created_at: datetime
    completed_at: datetime | None = None
    recipes: dict[OrderColor, RecipeRead]
    total_required: int
    total_started: int
    total_completed: int
    rejected_count: int
    active_operation: ActiveOperation | None = None
    completion_notified: bool = False


class ActualWpcRequest(BaseModel):
    actual_wpc: int = Field(..., description="ActualWPC из Hmi: 1 black, 2 red, 3 silver")


class CompleteRequest(BaseModel):
    counter_pip: bool = Field(True, description="Совместимость со старой симуляцией завершения")


class EventCounterRequest(BaseModel):
    value: int | None = Field(None, description="Опциональное явное значение счетчика")


class HmiState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    StartButton: bool = False
    ResetButton: bool = False
    BlackTags: int = 0
    RedTags: int = 0
    SilverTags: int = 0
    BlackCap: bool = False
    RedCap: bool = False
    SilverCap: bool = False
    ActualWPC: int = 0
    ColorDetectedCounter: int = 0
    FinishedTrayCounter: int = 0
    RejectTrayCounter: int = 0
    calibrated: bool = False


class DbMyDataState(BaseModel):
    WpcSlide1_Input: bool = False
    WpcSlide2_Input: bool = False
    WpcSlide1_Control: bool = False
    WpcSlide2_Control: bool = False


class PlcState(BaseModel):
    hmi: HmiState
    db_my_data: DbMyDataState
    current_operation: ActiveOperation | None = None


class ErpEventType(str, Enum):
    production_unit_start = "production_unit_start"
    customer_order_complete = "customer_order_complete"


class IntegrationEventRead(BaseModel):
    id: str
    event_type: ErpEventType
    payload: dict[str, Any]
    status: str
    created_at: datetime
    sent_at: datetime | None = None
    error: str | None = None
