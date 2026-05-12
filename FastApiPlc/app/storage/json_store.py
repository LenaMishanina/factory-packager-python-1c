from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_DATA = {"orders": [], "rejects": []}


class JsonStore:
    """Small async JSON store used until the real PLC/database integration appears."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def load(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._load_unlocked)

    async def save(self, data: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_unlocked, data)

    async def update(self, mutator):
        async with self._lock:
            data = await asyncio.to_thread(self._load_unlocked)
            result = mutator(data)
            await asyncio.to_thread(self._save_unlocked, data)
            return result

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return deepcopy(DEFAULT_DATA)
        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        data.setdefault("orders", [])
        data.setdefault("rejects", [])
        return data

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)
