"""比價流程入口（services.comparison.compare_prices）的測試：只換外部依賴（AI、平台轉接器、時鐘、快取）。"""

from datetime import datetime, timezone

import pytest

from services.cache import TTLCache
from services.comparison import GEMINI_VISION_PROMPT, PlatformStatus, compare_prices
from services.parser import GeminiServerError, IrrelevantPostError, ParsedItem
from services.platforms import FetchStatus
from services.pricing import convert_to_twd
from tests.fakes import FakeAdapter, FakeParser, failed, fake_platforms, found

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LINK_ONLY_PLATFORMS = ("yahoo_jp", "yahoo_tw", "taobao")


def fixed_clock():
    return FIXED_NOW


def jpy_to_twd(price):
    return int(round(convert_to_twd(price, currency="JPY")))


def switch_item(**overrides):
    fields = dict(
        franchise="任天堂",
        character="Switch OLED",
        item_type="主機",
        keyword_jp="Nintendo Switch",
        keyword_zh="Switch OLED",
        search_query_ja="Nintendo Switch",
        perfected_keyword="Nintendo Switch OLED",
        fb_price_twd=8500,
    )
    fields.update(overrides)
    return ParsedItem(**fields)


@pytest.fixture
def no_affiliates(monkeypatch):
    from config import settings

    for name in ("buyee_affiliate_id", "affiliate_base_url", "shopee_affiliate_base_url",
                 "taobao_affiliate_base_url", "yahoo_tw_affiliate_base_url"):
        monkeypatch.setattr(settings, name, None)
    for name in ("AFFILIATE_BASE_URL", "SHOPEE_AFFILIATE_BASE_URL", "TAOBAO_AFFILIATE_BASE_URL", "YAHOO_TW_AFFILIATE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


async def run(text=None, image=None, parser=None, platforms=None, cache=None):
    return await compare_prices(
        text=text,
        image=image,
        parser=parser or FakeParser(switch_item()),
        platforms=platforms or fake_platforms(),
        clock=fixed_clock,
        cache=cache if cache is not None else TTLCache(),
    )


@pytest.mark.anyio
async def test_success_returns_prices_per_platform_and_keywords():
    platforms = fake_platforms(
        mercari=found(30000.0, 35000.0, 40000.0),
        rakuten=found(37000.0),
        shopee=found(6990.0, currency="TWD"),
    )
    result = await run(text="switch oled", platforms=platforms)

    assert result.product_name == "Nintendo Switch OLED"
    assert result.keyword_zh == "Nintendo Switch OLED"
    assert result.keyword_jp == "Nintendo Switch"
    assert result.fetched_at == FIXED_NOW
    assert result.from_cache is False

    assert list(result.platforms) == ["mercari", "yahoo_jp", "rakuten", "shopee", "yahoo_tw", "taobao"]
    assert result.platforms["mercari"].status is PlatformStatus.OK
    assert result.platforms["mercari"].min_price_twd == jpy_to_twd(30000.0)
    assert result.platforms["rakuten"].min_price_twd == jpy_to_twd(37000.0)
    assert result.platforms["shopee"].min_price_twd == 6990
    for name in LINK_ONLY_PLATFORMS:
        assert result.platforms[name].status is PlatformStatus.LINK_ONLY
        assert result.platforms[name].min_price_twd is None


@pytest.mark.anyio
async def test_every_platform_carries_its_search_link(no_affiliates):
    result = await run(text="switch")

    assert result.platforms["mercari"].search_url == "https://buyee.jp/mercari/search?keyword=NINTENDO%20SWITCH"
    assert result.platforms["rakuten"].search_url.startswith("https://buyee.jp/rakuten/shopping/search/")
    assert result.platforms["yahoo_jp"].search_url == "https://buyee.jp/item/search/query/NINTENDO%20SWITCH"
    assert result.platforms["shopee"].search_url == "https://shopee.tw/search?keyword=NINTENDO%20SWITCH%20OLED"
    assert result.platforms["yahoo_tw"].search_url.startswith("https://tw.buy.yahoo.com/search/product?p=")


@pytest.mark.anyio
async def test_search_links_include_affiliate_parameters(no_affiliates, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "buyee_affiliate_id", "aff_tag_123")
    result = await run(text="switch")

    assert "af=aff_tag_123" in result.platforms["mercari"].search_url
    assert "af=aff_tag_123" in result.platforms["rakuten"].search_url


@pytest.mark.anyio
async def test_searches_each_platform_with_its_language_keyword():
    mercari, shopee = FakeAdapter(failed(FetchStatus.NO_RESULTS)), FakeAdapter(failed(FetchStatus.NO_RESULTS))
    parser = FakeParser(switch_item())
    await run(text="switch", parser=parser, platforms=fake_platforms(mercari=mercari, shopee=shopee))

    assert parser.calls == [dict(post_text="switch", image_data=None, vision_prompt=None)]
    assert mercari.calls == ["Nintendo Switch"]
    assert shopee.calls == ["Nintendo Switch OLED"]


@pytest.mark.anyio
async def test_image_input_is_sent_to_parser_with_vision_prompt():
    parser = FakeParser(switch_item())
    await run(image=b"jpeg-bytes", parser=parser)

    assert parser.calls == [
        dict(post_text=None, image_data=b"jpeg-bytes", vision_prompt=GEMINI_VISION_PROMPT)
    ]


@pytest.mark.anyio
async def test_requires_exactly_one_of_text_or_image():
    with pytest.raises(ValueError):
        await run()
    with pytest.raises(ValueError):
        await run(text="a", image=b"b")


@pytest.mark.anyio
async def test_partial_platform_failure_keeps_other_prices():
    platforms = fake_platforms(
        mercari=failed(FetchStatus.BLOCKED),
        rakuten=failed(FetchStatus.TIMEOUT),
        shopee=found(6990.0, currency="TWD"),
    )
    result = await run(text="switch", platforms=platforms)

    assert result.platforms["shopee"].min_price_twd == 6990
    assert result.platforms["rakuten"].status is PlatformStatus.TIMEOUT
    assert result.platforms["rakuten"].min_price_twd is None
    assert result.platforms["mercari"].status is PlatformStatus.FAILED
    assert result.platforms["mercari"].min_price_twd is None


@pytest.mark.anyio
async def test_adapter_raising_does_not_affect_other_platforms():
    platforms = fake_platforms(mercari=RuntimeError("boom"), rakuten=found(37000.0))
    result = await run(text="switch", platforms=platforms)

    assert result.platforms["mercari"].status is PlatformStatus.FAILED
    assert result.platforms["rakuten"].min_price_twd == jpy_to_twd(37000.0)


@pytest.mark.anyio
async def test_mercari_failure_gives_links_card_and_queries_each_platform_once():
    rakuten = FakeAdapter(found(18300.0))
    platforms = fake_platforms(mercari=failed(FetchStatus.TIMEOUT), rakuten=rakuten)
    result = await run(text="viscaria", platforms=platforms)

    assert result.is_full is False
    assert result.platforms["rakuten"].min_price_twd == jpy_to_twd(18300.0)
    assert len(rakuten.calls) == 1


@pytest.mark.anyio
async def test_mercari_listings_give_full_card_data():
    platforms = fake_platforms(
        mercari=found(30000.0, 35000.0, 40000.0, thumbnail="https://static.mercdn.net/x.jpg"),
    )
    result = await run(text="switch", platforms=platforms)

    assert result.is_full is True
    assert result.scraper_result.median_price_jpy == 35000.0
    assert result.scraper_result.representative_image_url == "https://static.mercdn.net/x.jpg"
    assert result.pricing.price_jpy == 35000.0
    assert result.pricing.fb_price_twd == 8500
    assert result.min_price_twd == jpy_to_twd(30000.0)
    assert result.avg_price_twd == jpy_to_twd(35000.0)


@pytest.mark.anyio
async def test_all_platforms_fail_yields_no_prices():
    platforms = fake_platforms(
        mercari=failed(FetchStatus.BLOCKED),
        rakuten=failed(FetchStatus.MALFORMED),
    )
    result = await run(text="藍牙耳機", platforms=platforms)

    assert all(q.min_price_twd is None for q in result.platforms.values())
    assert result.min_price_twd is None
    assert result.avg_price_twd is None


@pytest.mark.anyio
async def test_no_results_means_no_match_and_never_a_made_up_price():
    result = await run(text="switch")

    assert result.platforms["mercari"].status is PlatformStatus.NO_MATCH
    assert result.platforms["rakuten"].status is PlatformStatus.NO_MATCH
    assert all(q.min_price_twd is None for q in result.platforms.values())


@pytest.mark.anyio
async def test_rakuten_price_ignores_junk_below_300_yen_and_beyond_first_five():
    platforms = fake_platforms(rakuten=found(9000.0, 120.0, 8000.0, 9500.0, 8800.0, 500.0))
    result = await run(text="switch", platforms=platforms)

    assert result.platforms["rakuten"].min_price_twd == jpy_to_twd(8000.0)


@pytest.mark.anyio
async def test_irrelevant_input_falls_back_to_links_using_raw_text():
    parser = FakeParser(error=IrrelevantPostError("not shopping"))
    result = await run(text="今天天氣好", parser=parser)

    assert result.is_full is False
    assert result.parsed_item is None
    assert result.keyword_zh == "今天天氣好"
    assert result.keyword_jp == "人気商品"


@pytest.mark.anyio
async def test_ai_service_errors_propagate_to_caller():
    with pytest.raises(GeminiServerError):
        await run(text="switch", parser=FakeParser(error=GeminiServerError("busy")))


@pytest.mark.anyio
async def test_cache_hit_skips_ai_and_platforms_and_keeps_original_time():
    cache = TTLCache()
    first = await run(text="switch", cache=cache)

    parser = FakeParser(error=AssertionError("should not be called"))
    mercari = FakeAdapter(AssertionError("should not be called"))
    second = await compare_prices(
        text="  SWITCH ",
        parser=parser,
        platforms=fake_platforms(mercari=mercari),
        clock=lambda: datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc),
        cache=cache,
    )

    assert second.from_cache is True
    assert second.fetched_at == first.fetched_at
    assert second.platforms == first.platforms
    assert parser.calls == []
    assert mercari.calls == []


@pytest.mark.anyio
async def test_image_results_are_not_cached():
    cache = TTLCache()
    await run(image=b"img", cache=cache)
    assert len(cache) == 0
