from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import ActiveOperation, OrderColor, RecipeRead
from app.services.plc_stub import PlcStub


def recipe(color: OrderColor, *, tags: int, cap: bool, required: int = 1) -> RecipeRead:
    return RecipeRead(
        line_number=1,
        color=color,
        tags=tags,
        cap=cap,
        required=required,
        started=0,
        completed=0,
    )


def test_load_recipes_writes_product_parameters_to_hmi() -> None:
    plc = PlcStub()

    plc.load_recipes(
        {
            OrderColor.black: recipe(OrderColor.black, tags=1, cap=True),
            OrderColor.red: recipe(OrderColor.red, tags=2, cap=False),
        }
    )

    state = plc.state()
    assert state.hmi.BlackTags == 1
    assert state.hmi.BlackCap is True
    assert state.hmi.RedTags == 2
    assert state.hmi.RedCap is False
    assert state.hmi.SilverTags == 0
    assert state.hmi.SilverCap is False
    assert state.db_my_data.WpcSlide1_Control is True
    assert state.db_my_data.WpcSlide2_Control is False


def test_start_stop_and_reset_update_hmi_state() -> None:
    plc = PlcStub()

    plc.start()
    assert plc.state().hmi.StartButton is True

    plc.stop()
    assert plc.state().hmi.StartButton is False

    plc.reset()
    state = plc.state()
    assert state.hmi.ResetButton is False
    assert state.hmi.calibrated is True


def test_simulation_methods_increment_event_counters() -> None:
    plc = PlcStub()

    plc.simulate_color_detected(2)
    plc.simulate_finished_tray()
    plc.simulate_reject_tray()

    state = plc.state()
    assert state.hmi.ActualWPC == 2
    assert state.hmi.ColorDetectedCounter == 1
    assert state.hmi.FinishedTrayCounter == 1
    assert state.hmi.RejectTrayCounter == 1


def test_route_methods_set_control_outputs() -> None:
    plc = PlcStub()

    plc.route_to_reject()
    reject_state = plc.state()
    assert reject_state.db_my_data.WpcSlide1_Input is False
    assert reject_state.db_my_data.WpcSlide2_Input is True
    assert reject_state.db_my_data.WpcSlide1_Control is False
    assert reject_state.db_my_data.WpcSlide2_Control is True

    plc.route_to_finished()
    finished_state = plc.state()
    assert finished_state.db_my_data.WpcSlide1_Input is True
    assert finished_state.db_my_data.WpcSlide2_Input is False
    assert finished_state.db_my_data.WpcSlide1_Control is True
    assert finished_state.db_my_data.WpcSlide2_Control is False


def test_apply_operation_routes_and_keeps_current_operation() -> None:
    plc = PlcStub()
    operation = ActiveOperation(
        id="op-1",
        decision="accepted",
        actual_wpc=1,
        color="black",
        created_at=datetime.now(timezone.utc),
    )

    plc.apply_operation(operation)

    state = plc.state()
    assert state.current_operation == operation
    assert state.hmi.ActualWPC == 1
    assert state.db_my_data.WpcSlide1_Control is True

    plc.clear_operation()
    assert plc.state().current_operation is None
