from __future__ import annotations

from app.schemas import Color, DbMyDataState, HmiState, OperationState, PlcState


class PlcStub:
    def __init__(self) -> None:
        self.hmi = HmiState()
        self.db_my_data = DbMyDataState()
        self.current_operation: OperationState | None = None

    def start(self) -> None:
        self.hmi.StartButton = True

    def stop(self) -> None:
        self.hmi.StartButton = False

    def reset(self) -> None:
        self.hmi.ResetButton = True
        self.hmi.calibrated = True
        self.hmi.ResetButton = False

    def apply_operation(self, operation: OperationState, tags: int | None = None, cap: bool | None = None) -> None:
        self.current_operation = operation
        self.hmi.ActualWPC = operation.actual_wpc
        self._clear_product_fields()

        if operation.order_id and tags is not None and cap is not None:
            self._write_product(operation.color, tags, cap)
            self._route_to_finished()
        else:
            self._route_to_reject()

    def complete_current(self, operation: OperationState) -> None:
        self.current_operation = operation
        self.db_my_data = DbMyDataState()

    def clear_completed_operation(self) -> None:
        self.current_operation = None

    def state(self, rejected_count: int) -> PlcState:
        return PlcState(
            hmi=self.hmi,
            db_my_data=self.db_my_data,
            current_operation=self.current_operation,
            rejected_count=rejected_count,
        )

    def _clear_product_fields(self) -> None:
        self.hmi.BlackTags = 0
        self.hmi.RedTags = 0
        self.hmi.SilverTags = 0
        self.hmi.BlackCap = False
        self.hmi.RedCap = False
        self.hmi.SilverCap = False

    def _write_product(self, color: Color, tags: int, cap: bool) -> None:
        if color == Color.black:
            self.hmi.BlackTags = tags
            self.hmi.BlackCap = cap
        elif color == Color.red:
            self.hmi.RedTags = tags
            self.hmi.RedCap = cap
        else:
            self.hmi.SilverTags = tags
            self.hmi.SilverCap = cap

    def _route_to_finished(self) -> None:
        self.db_my_data.WpcSlide1_Input = True
        self.db_my_data.WpcSlide2_Input = False
        self.db_my_data.WpcSlide1_Control = True
        self.db_my_data.WpcSlide2_Control = False

    def _route_to_reject(self) -> None:
        self.db_my_data.WpcSlide1_Input = False
        self.db_my_data.WpcSlide2_Input = True
        self.db_my_data.WpcSlide1_Control = False
        self.db_my_data.WpcSlide2_Control = True
