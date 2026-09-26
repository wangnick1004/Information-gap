"""測試用假依賴：替換比價流程入口的 AI 解析器與平台抓價函式（不碰網路）。"""

import functools
from types import SimpleNamespace

from services.comparison import PlatformFetchers, compare_prices
from services.scraper import ScrapingResult


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


def returning(value):
    """回傳固定值（或拋出例外）的假非同步函式，並記錄呼叫參數於 .calls。"""
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(value, BaseException):
            raise value
        return value

    fake.calls = calls
    return fake


def buyee_result(sample_prices=(30000.0, 35000.0, 40000.0), search_url="https://buyee.jp/mercari/search?keyword=Switch"):
    prices = sorted(sample_prices)
    return ScrapingResult(
        query="query",
        search_url=search_url,
        lowest_price_jpy=prices[0] if prices else 0.0,
        median_price_jpy=prices[len(prices) // 2] if prices else 0.0,
        representative_image_url=None,
        sample_prices=list(sample_prices),
        total_found=len(prices),
    )


def fake_fetchers(**overrides):
    """預設所有平台都「查無價格」。"""
    fields = dict(
        buyee=returning(buyee_result(sample_prices=())),
        taiwanese=returning(SimpleNamespace(sample_prices=[])),
        chinese=returning(SimpleNamespace(sample_prices=[])),
        rakuten=returning(None),
        mercari=returning(None),
        shopee=returning(None),
    )
    fields.update(overrides)
    return PlatformFetchers(**fields)


def pipeline_with(parser, fetchers=None):
    """真實的比價流程入口，但換上假 AI 與假平台；用來 patch main.compare_prices。"""
    return functools.partial(compare_prices, parser=parser, fetchers=fetchers or fake_fetchers())
