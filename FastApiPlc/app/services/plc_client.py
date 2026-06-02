from __future__ import annotations

import asyncio

from app.schemas import ActiveOperation, DbMyDataState, HmiState, OrderColor, PlcState, RecipeRead


DB_HMI = 1
DB_ROUTE = 32


class Snap7PlcClient:
    """PLC client for Siemens S7-1500 data blocks used by the training stand."""

    def __init__(self, host: str, rack: int = 0, slot: int = 1) -> None:
        try:
            import snap7
            from snap7.util import get_bool, get_int, set_bool, set_int
        except ImportError as exc:  # pragma: no cover - depends on local PLC workstation
            raise RuntimeError("Install python-snap7 to use the real PLC client") from exc

        self._get_bool = get_bool
        self._get_int = get_int
        self._set_bool = set_bool
        self._set_int = set_int
        self._client = snap7.client.Client()
        self._client.connect(host, rack, slot)
        self.current_operation: ActiveOperation | None = None

    def load_recipes(self, recipes: dict[OrderColor, RecipeRead]) -> None:
        black = recipes.get(OrderColor.black)
        red = recipes.get(OrderColor.red)

        self._write_int(DB_HMI, 2, black.tags if black else 0)
        self._write_int(DB_HMI, 4, red.tags if red else 0)
        self._write_int(DB_HMI, 6, 0)
        self._write_bool(DB_HMI, 8, 0, bool(black and black.cap))
        self._write_bool(DB_HMI, 8, 1, bool(red and red.cap))
        self._write_bool(DB_HMI, 8, 2, False)
        self.route_to_finished()

    def start(self) -> None:
        self._write_bool(DB_HMI, 0, 0, True)

    def stop(self) -> None:
        self._write_bool(DB_HMI, 0, 0, False)

    def reset(self) -> None:
        self._write_bool(DB_HMI, 0, 1, True)
        self._write_bool(DB_HMI, 0, 1, False)

    def apply_operation(self, operation: ActiveOperation) -> None:
        self.current_operation = operation
        if operation.decision == "accepted":
            self.route_to_finished()
        else:
            self.route_to_reject()

    def clear_operation(self) -> None:
        self.current_operation = None

    def route_to_finished(self) -> None:
        self._write_bool(DB_ROUTE, 0, 2, True)
        self._write_bool(DB_ROUTE, 0, 3, False)

    def route_to_reject(self) -> None:
        self._write_bool(DB_ROUTE, 0, 2, False)
        self._write_bool(DB_ROUTE, 0, 3, True)

    def state(self) -> PlcState:
        hmi = HmiState(
            StartButton=self._read_bool(DB_HMI, 0, 0),
            ResetButton=self._read_bool(DB_HMI, 0, 1),
            BlackTags=self._read_int(DB_HMI, 2),
            RedTags=self._read_int(DB_HMI, 4),
            SilverTags=self._read_int(DB_HMI, 6),
            BlackCap=self._read_bool(DB_HMI, 8, 0),
            RedCap=self._read_bool(DB_HMI, 8, 1),
            SilverCap=self._read_bool(DB_HMI, 8, 2),
            ActualWPC=self._read_int(DB_HMI, 10),
            ColorDetectedCounter=self._read_int(DB_HMI, 12),
            FinishedTrayCounter=self._read_int(DB_HMI, 14),
            RejectTrayCounter=self._read_int(DB_HMI, 16),
        )
        route = DbMyDataState(
            WpcSlide1_Input=self._read_bool(DB_ROUTE, 0, 0),
            WpcSlide2_Input=self._read_bool(DB_ROUTE, 0, 1),
            WpcSlide1_Control=self._read_bool(DB_ROUTE, 0, 2),
            WpcSlide2_Control=self._read_bool(DB_ROUTE, 0, 3),
        )
        return PlcState(hmi=hmi, db_my_data=route, current_operation=self.current_operation)

    async def state_async(self) -> PlcState:
        return await asyncio.to_thread(self.state)

    def _read_int(self, db: int, offset: int) -> int:
        data = self._client.db_read(db, offset, 2)
        return self._get_int(data, 0)

    def _write_int(self, db: int, offset: int, value: int) -> None:
        data = bytearray(2)
        self._set_int(data, 0, value)
        self._client.db_write(db, offset, data)

    def _read_bool(self, db: int, byte_index: int, bit_index: int) -> bool:
        data = self._client.db_read(db, byte_index, 1)
        return self._get_bool(data, 0, bit_index)

    def _write_bool(self, db: int, byte_index: int, bit_index: int, value: bool) -> None:
        data = self._client.db_read(db, byte_index, 1)
        self._set_bool(data, 0, bit_index, value)
        self._client.db_write(db, byte_index, data)
