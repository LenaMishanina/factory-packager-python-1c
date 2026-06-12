from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import Color, LoadToHmiRequest, OrderColor, OrderLineRequest, normalize_color
from app.services.orders import parse_actual_wpc


def test_normalize_color_accepts_english_and_russian_aliases() -> None:
    assert normalize_color("black") == Color.black
    assert normalize_color("черная") == Color.black
    assert normalize_color("чёрный") == Color.black
    assert normalize_color("red") == Color.red
    assert normalize_color("красная") == Color.red
    assert normalize_color("красный") == Color.red


def test_order_line_rejects_silver_for_customer_order() -> None:
    with pytest.raises(ValidationError):
        OrderLineRequest(line_number=1, color="silver", tags=1, cap=False, quantity=1)


def test_order_line_rejects_too_many_tags() -> None:
    with pytest.raises(ValidationError):
        OrderLineRequest(line_number=1, color="black", tags=4, cap=False, quantity=1)


def test_load_to_hmi_rejects_duplicate_colors() -> None:
    with pytest.raises(ValidationError):
        LoadToHmiRequest(
            customer_order_number="ORDER-1",
            items=[
                OrderLineRequest(line_number=1, color=OrderColor.black, tags=1, cap=False, quantity=1),
                OrderLineRequest(line_number=2, color=OrderColor.black, tags=2, cap=True, quantity=1),
            ],
        )


def test_parse_actual_wpc_maps_plc_codes_to_colors() -> None:
    assert parse_actual_wpc(1) == Color.black
    assert parse_actual_wpc(2) == Color.red
    assert parse_actual_wpc(3) == Color.silver
    with pytest.raises(ValueError):
        parse_actual_wpc(9)
