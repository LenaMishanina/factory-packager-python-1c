from __future__ import annotations

from app.schemas import ActiveOperation, DbMyDataState, HmiState, OrderColor, PlcState, RecipeRead


class PlcStub:
    """In-memory PLC substitute used by tests and demos before connecting Snap7."""

    def __init__(self) -> None:
        self.hmi = HmiState()
        self.db_my_data = DbMyDataState()
        self.current_operation: ActiveOperation | None = None

    def load_recipes(self, recipes: dict[OrderColor, RecipeRead]) -> None:
        self._clear_product_fields()
        black = recipes.get(OrderColor.black)
        red = recipes.get(OrderColor.red)

        if black:
            self.hmi.BlackTags = black.tags
            self.hmi.BlackCap = black.cap
        if red:
            self.hmi.RedTags = red.tags
            self.hmi.RedCap = red.cap

        self.hmi.SilverTags = 0
        self.hmi.SilverCap = False
        self.route_to_finished()

    def start(self) -> None:
        self.hmi.StartButton = True

    def stop(self) -> None:
        self.hmi.StartButton = False

    def reset(self) -> None:
        self.hmi.ResetButton = True
        self.hmi.calibrated = True
        self.hmi.ResetButton = False

    def apply_operation(self, operation: ActiveOperation) -> None:
        self.current_operation = operation
        self.hmi.ActualWPC = operation.actual_wpc
        if operation.decision == "accepted":
            self.route_to_finished()
        else:
            self.route_to_reject()

    def clear_operation(self) -> None:
        self.current_operation = None

    def route_to_finished(self) -> None:
        self.db_my_data.WpcSlide1_Input = True
        self.db_my_data.WpcSlide2_Input = False
        self.db_my_data.WpcSlide1_Control = True
        self.db_my_data.WpcSlide2_Control = False

    def route_to_reject(self) -> None:
        self.db_my_data.WpcSlide1_Input = False
        self.db_my_data.WpcSlide2_Input = True
        self.db_my_data.WpcSlide1_Control = False
        self.db_my_data.WpcSlide2_Control = True

    def simulate_color_detected(self, actual_wpc: int) -> None:
        self.hmi.ActualWPC = actual_wpc
        self.hmi.ColorDetectedCounter += 1

    def simulate_finished_tray(self) -> None:
        self.hmi.FinishedTrayCounter += 1

    def simulate_reject_tray(self) -> None:
        self.hmi.RejectTrayCounter += 1

    def state(self) -> PlcState:
        return PlcState(hmi=self.hmi, db_my_data=self.db_my_data, current_operation=self.current_operation)

    def _clear_product_fields(self) -> None:
        self.hmi.BlackTags = 0
        self.hmi.RedTags = 0
        self.hmi.SilverTags = 0
        self.hmi.BlackCap = False
        self.hmi.RedCap = False
        self.hmi.SilverCap = False
