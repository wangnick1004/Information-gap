"""
時鐘：比價流程的截止時間、價格取得時間與快取過期都透過它取得時間。
測試以假時鐘（tests.fakes.FakeClock）替換，不必真的等待。
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """目前時間（含時區），用於「價格取得時間」。"""

    def monotonic(self) -> float:
        """單調遞增的秒數，用於計算耗時與截止時間。"""

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


system_clock = SystemClock()
