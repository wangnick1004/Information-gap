"""比價流程入口（services.comparison.compare_prices）的測試：只換外部依賴（AI、平台、時鐘、快取）。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.cache import TTLCache
from services.comparison import GEMINI_VISION_PROMPT, PlatformStatus, compare_prices
from services.parser import GeminiServerError, IrrelevantPostError, ParsedItem
from services.scraper import ScrapingError, ScrapingTimeoutError
from tests.fakes import FakeParser, buyee_result, fake_fetchers, returning

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def fixed_clock():
    return FIXED_NOW


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


async def run(text=None, image=None, parser=None, fetchers=None, cache=None):
    return await compare_prices(
        text=text,
        image=image,
        parser=parser or FakeParser(switch_item()),
        fetchers=fetchers or fake_fetchers(),
        clock=fixed_clock,
        cache=cache if cache is not None else TTLCache(),
    )


@pytest.mark.anyio
async def test_success_returns_prices_per_platform_and_keywords():
    fetchers = fake_fetchers(
        buyee=returning(buyee_result()),
        taiwanese=returning(SimpleNamespace(sample_prices=[9000.0])),
        rakuten=returning(7800),
        mercari=returning(7200),
        shopee=returning(6990),
    )
    result = await run(text="switch oled", fetchers=fetchers)

    assert result.product_name == "Nintendo Switch OLED"
    assert result.keyword_zh == "Nintendo Switch OLED"
    assert result.keyword_jp == "Nintendo Switch"
    assert result.search_url == "https://buyee.jp/mercari/search?keyword=Switch"
    assert result.fetched_at == FIXED_NOW
    assert result.from_cache is False

    assert result.platforms["shopee"].status is PlatformStatus.OK
    assert result.platforms["shopee"].min_price_twd == 6990
    assert result.platforms["rakuten"].min_price_twd == 7800
    assert result.platforms["mercari"].min_price_twd == 7200
    assert result.platforms["mercari"].is_lower_bound is True
    assert result.platforms["yahoo_tw"].min_price_twd == 9000
    assert result.platforms["taobao"].status is PlatformStatus.NO_MATCH
    assert result.platforms["taobao"].min_price_twd is None


@pytest.mark.anyio
async def test_searches_with_ai_keywords_not_raw_text():
    buyee = returning(buyee_result())
    shopee = returning(None)
    parser = FakeParser(switch_item())
    await run(text="switch", parser=parser, fetchers=fake_fetchers(buyee=buyee, shopee=shopee))

    assert parser.calls == [dict(post_text="switch", image_data=None, vision_prompt=None)]
    assert buyee.calls == [(("Nintendo Switch",), {"max_items": 15})]
    assert shopee.calls[0][0][0] == "Nintendo Switch OLED"


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
    fetchers = fake_fetchers(
        buyee=returning(buyee_result()),
        rakuten=returning(ScrapingTimeoutError("slow")),
        mercari=returning(RuntimeError("boom")),
        shopee=returning(6990),
    )
    result = await run(text="switch", fetchers=fetchers)

    assert result.platforms["shopee"].min_price_twd == 6990
    assert result.platforms["rakuten"].status is PlatformStatus.TIMEOUT
    assert result.platforms["rakuten"].min_price_twd is None
    # Mercari API 失敗時仍以 Buyee 抓到的價格補上
    assert result.platforms["mercari"].status is PlatformStatus.OK
    assert result.platforms["mercari"].is_lower_bound is False


@pytest.mark.anyio
async def test_buyee_failure_falls_back_to_links_and_keeps_fetched_prices():
    rakuten = returning(3837)
    fetchers = fake_fetchers(
        buyee=returning(ScrapingTimeoutError("slow", search_url="https://buyee.jp/x")),
        rakuten=rakuten,
    )
    result = await run(text="viscaria", fetchers=fetchers)

    assert result.is_full is False
    assert result.search_url == "https://buyee.jp/x"
    assert result.platforms["rakuten"].min_price_twd == 3837
    # 已取得的樂天價格不重抓
    assert len(rakuten.calls) == 1


@pytest.mark.anyio
async def test_all_platforms_fail_yields_no_prices():
    error = ScrapingError("blocked")
    fetchers = fake_fetchers(
        buyee=returning(error),
        taiwanese=returning(error),
        chinese=returning(error),
    )
    result = await run(text="藍牙耳機", fetchers=fetchers)

    assert all(q.min_price_twd is None for q in result.platforms.values())
    assert result.min_price_twd is None
    assert result.avg_price_twd is None


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
    buyee = returning(AssertionError("should not be called"))
    second = await compare_prices(
        text="  SWITCH ",
        parser=parser,
        fetchers=fake_fetchers(buyee=buyee),
        clock=lambda: datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc),
        cache=cache,
    )

    assert second.from_cache is True
    assert second.fetched_at == first.fetched_at
    assert second.platforms == first.platforms
    assert parser.calls == []


@pytest.mark.anyio
async def test_image_results_are_not_cached():
    cache = TTLCache()
    await run(image=b"img", cache=cache)
    assert len(cache) == 0
