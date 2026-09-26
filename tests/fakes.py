"""測試用假依賴：替換比價流程入口的 AI 解析器與平台轉接器（不碰網路）。"""

import asyncio
import base64
import functools
import hashlib
import heapq
import hmac
from dataclasses import replace
from datetime import timedelta

from services.comparison import compare_prices
from services.platforms import FetchResult, FetchStatus, Listing, build_platforms


class FakeParser:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class FakeAdapter:
    """假平台轉接器：回傳固定的 FetchResult（或拋出例外），並記錄查詢關鍵字於 .calls。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    async def search(self, keyword, timeout):
        self.calls.append(keyword)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def found(*prices, currency="JPY", thumbnail=None):
    return FetchResult(FetchStatus.OK, tuple(
        Listing(title=f"item {i}", price=price, currency=currency, url=f"https://example.com/{i}", thumbnail_url=thumbnail)
        for i, price in enumerate(prices)
    ))


def failed(status):
    return FetchResult(status)


def fake_platforms(evaluation_mode=False, **adapters):
    """
    真實的平台集合，但轉接器換成假的。未指定的平台：有轉接器者一律「查無結果」，無轉接器者維持僅連結。
    指定值可為 FetchResult、例外或 FakeAdapter；也可替僅連結的平台（例如蝦皮）掛上假轉接器。
    """
    platforms = build_platforms(evaluation_mode=evaluation_mode)
    fakes = {}
    for name, platform in platforms.items():
        if name in adapters:
            given = adapters[name]
            adapter = given if isinstance(given, FakeAdapter) else FakeAdapter(given)
        elif platform.adapter is not None:
            adapter = FakeAdapter(FetchResult(FetchStatus.NO_RESULTS))
        else:
            adapter = None
        fakes[name] = replace(platform, adapter=adapter)
    return fakes


def pipeline_with(parser, platforms=None):
    """真實的比價流程入口，但換上假 AI 與假平台；用來 patch main.compare_prices。"""
    return functools.partial(compare_prices, parser=parser, platforms=platforms or fake_platforms())


def generate_signature(secret: str, body: str) -> str:
    """計算 LINE webhook 請求的 X-Line-Signature（HMAC-SHA256）。"""
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FakeClock:
    """
    假時鐘（虛擬時間）：sleep 不真的等待。所有待命中的工作都卡在 sleep 時，
    時間直接跳到最早的喚醒點，因此「15 秒截止」「1 小時快取」都能瞬間測完。
    """

    def __init__(self, start):
        self._start = start
        self._elapsed = 0.0
        self._sleepers = []
        self._seq = 0
        self._ticker = None

    def now(self):
        return self._start + timedelta(seconds=self._elapsed)

    def monotonic(self):
        return self._elapsed

    def advance(self, seconds):
        self._elapsed += seconds

    async def sleep(self, seconds):
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._seq += 1
        heapq.heappush(self._sleepers, (self._elapsed + max(0.0, seconds), self._seq, waiter))
        if self._ticker is None or self._ticker.done():
            self._ticker = loop.create_task(self._tick())
        await waiter

    async def _tick(self):
        while self._sleepers:
            # 先讓其他立即可完成的工作跑完，再把時間推進到下一個喚醒點
            for _ in range(50):
                await asyncio.sleep(0)
            wake_at, _, waiter = heapq.heappop(self._sleepers)
            if waiter.done():
                continue
            self._elapsed = max(self._elapsed, wake_at)
            waiter.set_result(None)


class SlowAdapter(FakeAdapter):
    """假慢平台：在假時鐘上等 delay 秒後才回傳。"""

    def __init__(self, clock, delay, result):
        super().__init__(result)
        self.clock = clock
        self.delay = delay

    async def search(self, keyword, timeout):
        self.calls.append(keyword)
        await self.clock.sleep(self.delay)
        return self.result


class SlowParser(FakeParser):
    """假慢 AI 解析器：在假時鐘上等 delay 秒後才回傳。"""

    def __init__(self, clock, delay, result=None, error=None):
        super().__init__(result, error)
        self.clock = clock
        self.delay = delay

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        await self.clock.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result
