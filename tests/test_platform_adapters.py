"""平台轉接器（測試切入點 3）：以錄下的真實平台回應取代網路，驗證解析與失敗狀態。"""

import asyncio
import json
from pathlib import Path

import pytest

from services.platforms import (
    BuyeeMercariAdapter,
    BuyeeRakutenAdapter,
    FetchStatus,
    MercariRapidApiAdapter,
    ShopeeRapidApiAdapter,
    build_platforms,
    search_safely,
)

FIXTURES = Path(__file__).parent / "fixtures" / "platforms"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def http_returning(status, body):
    """假 HTTP GET：回傳錄下的回應，並記錄請求網址。"""
    calls = []

    async def get(url, headers, timeout):
        calls.append(url)
        return status, body

    get.calls = calls
    return get


def http_raising(error):
    async def get(url, headers, timeout):
        raise error

    return get


# --- Buyee Mercari ---

@pytest.mark.anyio
async def test_buyee_mercari_parses_recorded_search_page():
    http = http_returning(200, fixture("buyee_mercari_search.html"))
    result = await BuyeeMercariAdapter(http=http).search("Nintendo Switch", timeout=5)

    assert result.status is FetchStatus.OK
    assert len(result.listings) == 8
    first = result.listings[0]
    assert first.title == "任天堂スイッチソフト ニンジャボックス"
    assert first.price == 1450
    assert first.currency == "JPY"
    assert first.url == "https://buyee.jp/mercari/item/m75631237123?conversionType=Mercari_DirectSearch"
    assert first.thumbnail_url.startswith("https://static.mercdn.net/thumb/item/jpeg/m75631237123_1.jpg")
    assert result.listings[4].price == 18900
    assert http.calls == ["https://buyee.jp/mercari/search?keyword=NINTENDO%20SWITCH"]


@pytest.mark.anyio
async def test_buyee_mercari_empty_page_is_no_results():
    html = '<html><body><div class="category"></div></body></html>'
    result = await BuyeeMercariAdapter(http=http_returning(200, html)).search("zzqqxx", timeout=5)
    assert result.status is FetchStatus.NO_RESULTS
    assert result.listings == ()


@pytest.mark.anyio
async def test_buyee_mercari_anti_bot_challenge_is_blocked():
    # 2026-09-26 實測：不經瀏覽器直接請求 Buyee Mercari 搜尋頁，回傳 HTTP 202 與空內容
    result = await BuyeeMercariAdapter(http=http_returning(202, "")).search("Nintendo Switch", timeout=5)
    assert result.status is FetchStatus.BLOCKED


@pytest.mark.anyio
async def test_buyee_mercari_cards_without_prices_are_malformed():
    html = '<ul class="item-lists"><li class="list"><a href="/mercari/item/m1"><h2 class="name">x</h2></a></li></ul>'
    result = await BuyeeMercariAdapter(http=http_returning(200, html)).search("x", timeout=5)
    assert result.status is FetchStatus.MALFORMED


# --- Buyee 日本樂天 ---

@pytest.mark.anyio
async def test_buyee_rakuten_parses_recorded_search_page():
    http = http_returning(200, fixture("buyee_rakuten_search.html"))
    result = await BuyeeRakutenAdapter(http=http).search("Nintendo Switch 2", timeout=5)

    assert result.status is FetchStatus.OK
    assert len(result.listings) == 8
    first = result.listings[0]
    assert first.title == "Nintendo Switch 2（日本語・国内専用）"
    assert first.price == 59979
    assert first.currency == "JPY"
    assert first.url == "https://buyee.jp/rakuten/detail/book%3A21617106?conversionType=service_page_search"
    assert first.thumbnail_url.startswith("https://cdnrakuten.buyee.jp/")
    assert [item.price for item in result.listings[:3]] == [59979, 50700, 8339]
    # 樂天搜尋會把「Switch 2」併成「Switch2」
    assert http.calls == ["https://buyee.jp/rakuten/shopping/search/category/0?query=NINTENDO%20SWITCH2"]


@pytest.mark.anyio
async def test_buyee_rakuten_recorded_no_results_page():
    http = http_returning(200, fixture("buyee_rakuten_no_results.html"))
    result = await BuyeeRakutenAdapter(http=http).search("zzqqxxnotexist12345", timeout=5)
    assert result.status is FetchStatus.NO_RESULTS


@pytest.mark.anyio
async def test_buyee_rakuten_forbidden_is_blocked():
    result = await BuyeeRakutenAdapter(http=http_returning(403, "denied")).search("x", timeout=5)
    assert result.status is FetchStatus.BLOCKED


@pytest.mark.anyio
async def test_buyee_rakuten_non_html_is_malformed():
    result = await BuyeeRakutenAdapter(http=http_returning(200, "")).search("x", timeout=5)
    assert result.status is FetchStatus.MALFORMED


# --- Mercari（RapidAPI，僅評測模式）---

@pytest.mark.anyio
async def test_mercari_rapidapi_recorded_response_has_no_mercari_listings():
    # 2026-09-26 實錄：即使指定 platform=mercari，API 回傳的仍全是 goat / ebay 商品
    http = http_returning(200, fixture("rapidapi_mercari_search.json"))
    result = await MercariRapidApiAdapter(api_key="k", http=http).search("Nintendo Switch", timeout=5)
    assert result.status is FetchStatus.NO_RESULTS


@pytest.mark.anyio
async def test_mercari_rapidapi_keeps_only_mercari_listings():
    recorded = json.loads(fixture("rapidapi_mercari_search.json"))
    mercari_listing = dict(recorded["listings"][3], platform="mercari", url="https://jp.mercari.com/item/m1")
    recorded["listings"].append(mercari_listing)
    http = http_returning(200, json.dumps(recorded))

    result = await MercariRapidApiAdapter(api_key="k", http=http).search("Nintendo Switch", timeout=5)

    assert result.status is FetchStatus.OK
    assert len(result.listings) == 1
    listing = result.listings[0]
    assert listing.title == mercari_listing["title"]
    assert listing.price == mercari_listing["price"]
    assert listing.currency == "USD"
    assert listing.url == "https://jp.mercari.com/item/m1"


@pytest.mark.anyio
async def test_mercari_rapidapi_rate_limit_is_blocked():
    http = http_returning(429, '{"message": "Too many requests"}')
    result = await MercariRapidApiAdapter(api_key="k", http=http).search("x", timeout=5)
    assert result.status is FetchStatus.BLOCKED


@pytest.mark.anyio
async def test_mercari_rapidapi_non_json_is_malformed():
    http = http_returning(200, "<html>oops</html>")
    result = await MercariRapidApiAdapter(api_key="k", http=http).search("x", timeout=5)
    assert result.status is FetchStatus.MALFORMED


@pytest.mark.anyio
async def test_mercari_rapidapi_without_key_fails_without_request():
    http = http_returning(200, "{}")
    result = await MercariRapidApiAdapter(api_key=None, http=http).search("x", timeout=5)
    assert result.status is FetchStatus.FAILED
    assert http.calls == []


# --- 蝦皮（RapidAPI，僅評測模式）---

@pytest.mark.anyio
async def test_shopee_rapidapi_recorded_404_is_failed():
    # 2026-09-26 實錄：目前設定的蝦皮 RapidAPI 端點回傳 HTTP 404（Example Domain 頁）
    http = http_returning(404, fixture("rapidapi_shopee_404.html"))
    result = await ShopeeRapidApiAdapter(api_key="k", http=http).search("Nintendo Switch", timeout=5)
    assert result.status is FetchStatus.FAILED


@pytest.mark.anyio
async def test_shopee_rapidapi_parses_items_in_twd():
    body = json.dumps({"items": [
        {"item_basic": {"name": "Switch OLED 主機", "price": 899000000, "shopid": 7, "itemid": 42}},
        {"title": "Switch 保護貼", "price": "NT$199"},
    ]})
    result = await ShopeeRapidApiAdapter(api_key="k", http=http_returning(200, body)).search("switch", timeout=5)

    assert result.status is FetchStatus.OK
    assert [(item.title, item.price, item.currency) for item in result.listings] == [
        ("Switch OLED 主機", 8990, "TWD"),
        ("Switch 保護貼", 199, "TWD"),
    ]
    assert result.listings[0].url == "https://shopee.tw/product/7/42"


@pytest.mark.anyio
async def test_shopee_rapidapi_non_json_is_malformed():
    result = await ShopeeRapidApiAdapter(api_key="k", http=http_returning(200, "not json")).search("x", timeout=5)
    assert result.status is FetchStatus.MALFORMED


# --- 所有轉接器共通：不拋出未處理例外 ---

ALL_ADAPTERS = [
    lambda http: BuyeeMercariAdapter(http=http),
    lambda http: BuyeeRakutenAdapter(http=http),
    lambda http: MercariRapidApiAdapter(api_key="k", http=http),
    lambda http: ShopeeRapidApiAdapter(api_key="k", http=http),
]


@pytest.mark.anyio
@pytest.mark.parametrize("make_adapter", ALL_ADAPTERS)
async def test_network_timeout_is_timeout_status(make_adapter):
    result = await make_adapter(http_raising(asyncio.TimeoutError())).search("x", timeout=5)
    assert result.status is FetchStatus.TIMEOUT


@pytest.mark.anyio
@pytest.mark.parametrize("make_adapter", ALL_ADAPTERS)
async def test_unexpected_error_is_failed_status(make_adapter):
    result = await make_adapter(http_raising(RuntimeError("boom"))).search("x", timeout=5)
    assert result.status is FetchStatus.FAILED


@pytest.mark.anyio
async def test_search_safely_enforces_timeout_and_catches_errors():
    class Slow:
        async def search(self, keyword, timeout):
            await asyncio.sleep(10)

    class Broken:
        async def search(self, keyword, timeout):
            raise RuntimeError("boom")

    assert (await search_safely(Slow(), "x", timeout=0.01)).status is FetchStatus.TIMEOUT
    assert (await search_safely(Broken(), "x", timeout=1)).status is FetchStatus.FAILED


# --- 平台集合 ---

def test_platform_collection_covers_every_platform_in_category_table():
    from services.categories import CATEGORY_PLATFORMS

    platforms = build_platforms(evaluation_mode=False)
    assert set(platforms) == {name for names in CATEGORY_PLATFORMS.values() for name in names}
    assert all(len(names) == len(set(names)) == 6 for names in CATEGORY_PLATFORMS.values())


def test_rapidapi_adapters_are_disabled_outside_evaluation_mode():
    platforms = build_platforms(evaluation_mode=False)
    adapters = [p.adapter for p in platforms.values() if p.adapter is not None]
    assert not any(isinstance(a, (MercariRapidApiAdapter, ShopeeRapidApiAdapter)) for a in adapters)
    assert isinstance(platforms["mercari"].adapter, BuyeeMercariAdapter)
    assert isinstance(platforms["rakuten"].adapter, BuyeeRakutenAdapter)
    assert platforms["shopee"].adapter is None


def test_rapidapi_adapters_enabled_in_evaluation_mode():
    platforms = build_platforms(evaluation_mode=True)
    assert isinstance(platforms["mercari"].adapter, MercariRapidApiAdapter)
    assert isinstance(platforms["shopee"].adapter, ShopeeRapidApiAdapter)


def test_platform_search_urls_carry_affiliate_parameters(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "buyee_affiliate_id", "aff123")
    monkeypatch.setattr(settings, "shopee_affiliate_base_url", "https://s.shopee.tw/an_redir")
    platforms = build_platforms(evaluation_mode=False)

    assert "af=aff123" in platforms["mercari"].search_url("Nintendo Switch")
    assert platforms["shopee"].search_url("任天堂 Switch").startswith("https://s.shopee.tw/an_redir?")
