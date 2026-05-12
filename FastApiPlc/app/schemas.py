from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SIEMENS_INT_MIN = -32768
SIEMENS_INT_MAX = 32767
AVAILABLE_TAGS = 5


class Color(str, Enum):
    black = "black"
    red = "red"
    silver = "silver"


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
    if isinstance(value, str):
        key = value.strip().lower()
        if key in COLOR_ALIASES:
            return COLOR_ALIASES[key]
    raise ValueError("Color must be black/red/silver or черная/красная/серебристая")


class ApiResponse(BaseModel):
    success: bool = Field(..., examples=[True])
    message: str = Field(..., examples=["Заказ клиента был добавлен"])
    data: Any | None = None


class OrderProductRequest(BaseModel):
    color: Color = Field(..., description="Цвет емкости: black/red/silver или русское значение")
    tags: int = Field(
        0,
        ge=SIEMENS_INT_MIN,
        le=SIEMENS_INT_MAX,
        description="Количество фишек в диапазоне Siemens Int. Физически сейчас доступно 5.",
    )
    cap: bool = Field(False, description="Нужно ли закрыть емкость крышкой")
    quantity: int = Field(1, ge=1, description="Количество одинаковых изделий в заказе")

    @field_validator("color", mode="before")
    @classmethod
    def validate_color(cls, value: Any) -> Color:
        return normalize_color(value)


class OrderCreateRequest(BaseModel):
    document_number: str = Field(..., min_length=1, description="Номер документа «Заказ клиента» в 1C")
    items: list[OrderProductRequest] = Field(..., min_length=1, description="Позиции заказа клиента")


class OrderItemStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"


class OrderStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"


class OrderUnit(BaseModel):
    id: str
    source_line: int
    sequence: int
    color: Color
    tags: int
    cap: bool
    status: OrderItemStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None


class OrderRead(BaseModel):
    id: str
    document_number: str
    status: OrderStatus
    created_at: datetime
    total_items: int
    pending_items: int
    processing_items: int
    completed_items: int
    items: list[OrderUnit]


class ActualWpcRequest(BaseModel):
    actual_wpc: int = Field(..., description="ActualWPC из Hmi: 1/11 black, 2/12 red, 3/13 silver")


class ActualWpcInfo(BaseModel):
    actual_wpc: int
    color: Color
    cap_detected: bool


class CompleteRequest(BaseModel):
    counter_pip: bool = Field(
        True,
        description="Имитация датчика завершения. True означает, что емкость дошла до лотка.",
    )


class Decision(str, Enum):
    accepted = "accepted"
    rejected = "rejected"


class OperationState(BaseModel):
    id: str
    decision: Decision
    actual_wpc: int
    color: Color
    cap_detected: bool
    order_id: str | None = None
    item_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


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
    calibrated: bool = False


class DbMyDataState(BaseModel):
    WpcSlide1_Input: bool = False
    WpcSlide2_Input: bool = False
    WpcSlide1_Control: bool = False
    WpcSlide2_Control: bool = False


class PlcState(BaseModel):
    hmi: HmiState
    db_my_data: DbMyDataState
    current_operation: OperationState | None = None
    rejected_count: int = 0
    available_tags: Literal[5] = AVAILABLE_TAGS
