"""測試用假依賴：替換比價流程入口的 AI 解析器與平台轉接器（不碰網路）。"""

import base64
import functools
import hashlib
import hmac
from dataclasses import replace

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
