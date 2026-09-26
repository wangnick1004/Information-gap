"""比價流程入口（services.comparison.compare_prices）的測試：只換外部依賴（AI、平台轉接器、時鐘、快取）。"""

from datetime import datetime, timedelta, timezone

import pytest

from services.cache import TTLCache
from services.comparison import (
    DEADLINE_SECONDS,
    GEMINI_VISION_PROMPT,
    AiTimeoutError,
    PlatformStatus,
    compare_prices,
)
from services.parser import (
    GeminiAPIError,
    GeminiRateLimitError,
    GeminiServerError,
    IrrelevantPostError,
    ParsedItem,
)
from services.platforms import FetchStatus
from services.pricing import convert_to_twd
from tests.fakes import FakeAdapter, FakeClock, FakeParser, SlowAdapter, SlowParser, failed, fake_platforms, found

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
LINK_ONLY_PLATFORMS = ("yahoo_jp", "ruten", "taobao")


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
        # 含 Mercari、日本樂天、蝦皮（目前有轉接器的平台）的類別
        category="動漫周邊/玩具",
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


async def run(text=None, image=None, parser=None, platforms=None, cache=None, clock=None, received_at=None):
    return await compare_prices(
        text=text,
        image=image,
        parser=parser or FakeParser(switch_item()),
        platforms=platforms or fake_platforms(),
        clock=clock or FakeClock(FIXED_NOW),
        cache=cache if cache is not None else TTLCache(),
        received_at=received_at,
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

    # 依價格由低到高：Mercari（約 6,4xx）< 蝦皮 6,990 < 日本樂天；無價格者依對照表順序排在後面
    assert list(result.platforms) == ["mercari", "shopee", "rakuten", "yahoo_jp", "ruten", "taobao"]
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
    assert result.platforms["ruten"].search_url == "https://www.ruten.com.tw/find/?q=NINTENDO%20SWITCH%20OLED"


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


# --- AI 故障降級（工作票 07）：以原始輸入列出「其他」類 6 平台的搜尋連結 ---

AI_FAILURES = {
    "server_error_5xx": GeminiServerError("busy"),
    "rate_limit_429": GeminiRateLimitError("slow down"),
    "quota_exhausted": GeminiRateLimitError("RESOURCE_EXHAUSTED: quota exceeded"),
    "other_api_error": GeminiAPIError("bad response"),
    "timeout": AiTimeoutError(),
}


@pytest.mark.anyio
@pytest.mark.parametrize("error", AI_FAILURES.values(), ids=AI_FAILURES.keys())
async def test_ai_failure_degrades_to_default_platform_links_using_raw_input(no_affiliates, error):
    mercari, shopee = FakeAdapter(found(30000.0)), FakeAdapter(found(9000, currency="TWD"))
    platforms = fake_platforms(mercari=mercari, shopee=shopee)
    result = await run(text="aj1 芝加哥", parser=FakeParser(error=error), platforms=platforms)

    assert result.ai_unavailable is True
    assert result.parsed_item is None
    assert result.keyword_zh == result.keyword_jp == "aj1 芝加哥"
    assert result.category.value == "其他"
    assert list(result.platforms) == ["shopee", "momo", "pchome", "yahoo_tw", "ruten", "taobao"]
    assert all(q.status is PlatformStatus.LINK_ONLY and q.min_price_twd is None for q in result.platforms.values())
    assert result.platforms["shopee"].search_url == "https://shopee.tw/search?keyword=AJ1%20%E8%8A%9D%E5%8A%A0%E5%93%A5"
    # 只給連結，不查平台
    assert shopee.calls == [] and mercari.calls == []


@pytest.mark.anyio
async def test_normal_results_are_not_marked_ai_unavailable():
    assert (await run(text="switch")).ai_unavailable is False


@pytest.mark.anyio
async def test_degraded_results_are_not_cached():
    cache = TTLCache()
    await run(text="switch", parser=FakeParser(error=GeminiServerError("busy")), cache=cache)
    assert len(cache) == 0

    result = await run(text="switch", cache=cache)
    assert result.ai_unavailable is False


@pytest.mark.anyio
async def test_ai_failure_on_image_input_propagates_since_there_is_no_keyword():
    with pytest.raises(GeminiRateLimitError):
        await run(image=b"img", parser=FakeParser(error=GeminiRateLimitError("slow down")))


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
        clock=FakeClock(datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)),
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


# --- 類別 → 6 平台（對照表內容須與規格書 User Stories 8–13 一致）---

EXPECTED_PLATFORMS_BY_CATEGORY = {
    "3C 家電": ["pchome", "momo", "shopee", "yahoo_tw", "ruten", "rakuten"],
    "美妝保養": ["momo", "shopee", "pchome", "yahoo_tw", "rakuten", "taobao"],
    "服飾鞋包": ["shopee", "momo", "taobao", "ruten", "mercari", "rakuten"],
    "動漫周邊/玩具": ["mercari", "yahoo_jp", "rakuten", "shopee", "ruten", "taobao"],
    "運動戶外": ["momo", "pchome", "shopee", "yahoo_tw", "rakuten", "taobao"],
    "其他": ["shopee", "momo", "pchome", "yahoo_tw", "ruten", "taobao"],
}


@pytest.mark.anyio
@pytest.mark.parametrize("category, expected", EXPECTED_PLATFORMS_BY_CATEGORY.items())
async def test_each_category_selects_its_six_platforms(category, expected):
    result = await run(text="商品", parser=FakeParser(switch_item(category=category)))

    assert list(result.platforms) == expected
    assert result.category.value == category


@pytest.mark.anyio
async def test_undetermined_category_uses_default_platforms():
    result = await run(text="商品", parser=FakeParser(ParsedItem(keyword_zh="某商品")))

    assert result.category.value == "其他"
    assert list(result.platforms) == EXPECTED_PLATFORMS_BY_CATEGORY["其他"]


@pytest.mark.anyio
async def test_irrelevant_input_uses_default_platforms():
    result = await run(text="今天天氣好", parser=FakeParser(error=IrrelevantPostError("not shopping")))

    assert list(result.platforms) == EXPECTED_PLATFORMS_BY_CATEGORY["其他"]


@pytest.mark.anyio
async def test_platforms_outside_the_category_are_not_queried():
    mercari, rakuten = FakeAdapter(found(30000.0)), FakeAdapter(found(37000.0))
    platforms = fake_platforms(mercari=mercari, rakuten=rakuten)
    result = await run(text="吹風機", parser=FakeParser(switch_item(category="美妝保養")), platforms=platforms)

    assert "mercari" not in result.platforms
    assert mercari.calls == []
    assert rakuten.calls == ["Nintendo Switch"]


@pytest.mark.anyio
async def test_platforms_without_adapter_are_link_only_with_search_link(no_affiliates):
    result = await run(text="ps5", parser=FakeParser(switch_item(category="3C 家電")))

    for name in ("pchome", "momo", "yahoo_tw", "ruten"):
        assert result.platforms[name].status is PlatformStatus.LINK_ONLY
        assert result.platforms[name].min_price_twd is None
    assert result.platforms["pchome"].search_url == "https://24h.pchome.com.tw/search/?q=NINTENDO%20SWITCH%20OLED"
    assert result.platforms["momo"].search_url == (
        "https://www.momoshop.com.tw/search/searchShop.jsp?keyword=NINTENDO%20SWITCH%20OLED"
    )
    assert result.platforms["ruten"].search_url == "https://www.ruten.com.tw/find/?q=NINTENDO%20SWITCH%20OLED"
    assert result.platforms["yahoo_tw"].search_url == "https://tw.buy.yahoo.com/search/product?p=NINTENDO%20SWITCH%20OLED"


# --- 15 秒截止（假時鐘＋假慢平台，不真的等待）---

def anime_platforms(clock, **delays_and_results):
    """動漫類有轉接器的平台（mercari、rakuten）設定延遲與回傳。"""
    return fake_platforms(**{
        name: SlowAdapter(clock, delay, result) for name, (delay, result) in delays_and_results.items()
    })


@pytest.mark.anyio
async def test_reply_is_ready_within_deadline_even_if_a_platform_hangs():
    clock = FakeClock(FIXED_NOW)
    platforms = anime_platforms(clock, mercari=(600, found(30000.0)), rakuten=(2, found(37000.0)))

    result = await run(text="switch", platforms=platforms, clock=clock)

    assert clock.monotonic() <= DEADLINE_SECONDS
    assert result.platforms["rakuten"].status is PlatformStatus.OK
    assert result.platforms["rakuten"].min_price_twd == jpy_to_twd(37000.0)
    assert result.platforms["mercari"].status is PlatformStatus.TIMEOUT
    assert result.platforms["mercari"].min_price_twd is None
    assert result.platforms["mercari"].search_url.startswith("https://buyee.jp/mercari/search")


@pytest.mark.anyio
async def test_waits_for_slow_platforms_that_finish_before_deadline():
    clock = FakeClock(FIXED_NOW)
    platforms = anime_platforms(clock, mercari=(12, found(30000.0)), rakuten=(1, found(37000.0)))

    result = await run(text="switch", platforms=platforms, clock=clock)

    assert result.platforms["mercari"].status is PlatformStatus.OK
    assert result.platforms["mercari"].min_price_twd == jpy_to_twd(30000.0)
    assert clock.monotonic() == 12


@pytest.mark.anyio
async def test_does_not_wait_for_deadline_when_all_platforms_are_done():
    clock = FakeClock(FIXED_NOW)
    platforms = anime_platforms(clock, mercari=(3, found(30000.0)), rakuten=(1, found(37000.0)))

    await run(text="switch", platforms=platforms, clock=clock)

    assert clock.monotonic() == 3


@pytest.mark.anyio
async def test_slow_ai_parsing_counts_against_the_same_deadline():
    clock = FakeClock(FIXED_NOW)
    parser = SlowParser(clock, 6, switch_item())
    # 解析 6 秒後，9 秒的平台會超過 15 秒上限
    platforms = anime_platforms(clock, mercari=(600, found(30000.0)), rakuten=(9, found(37000.0)))

    result = await run(text="switch", parser=parser, platforms=platforms, clock=clock)

    assert clock.monotonic() <= DEADLINE_SECONDS
    assert result.platforms["rakuten"].status is PlatformStatus.TIMEOUT
    assert result.platforms["mercari"].status is PlatformStatus.TIMEOUT


@pytest.mark.anyio
async def test_time_before_the_pipeline_counts_against_the_deadline():
    """截止時間自收到訊息起算（例如下載圖片已花掉的時間）。"""
    clock = FakeClock(FIXED_NOW)
    received_at = clock.monotonic()
    clock.advance(10)
    platforms = anime_platforms(clock, mercari=(600, found(30000.0)), rakuten=(8, found(37000.0)))

    result = await run(text="switch", platforms=platforms, clock=clock, received_at=received_at)

    assert clock.monotonic() - received_at <= DEADLINE_SECONDS
    assert result.platforms["rakuten"].status is PlatformStatus.TIMEOUT


@pytest.mark.anyio
async def test_hanging_ai_parser_gives_up_within_budget_and_degrades_to_links():
    clock = FakeClock(FIXED_NOW)
    parser = SlowParser(clock, 600, switch_item())
    mercari = FakeAdapter(found(30000.0))

    result = await run(text="switch", parser=parser, platforms=fake_platforms(mercari=mercari), clock=clock)

    assert result.ai_unavailable is True
    assert clock.monotonic() < DEADLINE_SECONDS
    assert mercari.calls == []


@pytest.mark.anyio
async def test_slow_failing_ai_still_degrades_within_deadline():
    """AI 在收到訊息後很晚才失敗，降級回覆仍在 15 秒內備妥。"""
    clock = FakeClock(FIXED_NOW)
    clock.advance(5)
    parser = SlowParser(clock, 6.5, error=GeminiServerError("busy"))

    result = await run(text="switch", parser=parser, clock=clock, received_at=0.0)

    assert result.ai_unavailable is True
    assert clock.monotonic() <= DEADLINE_SECONDS


@pytest.mark.anyio
async def test_cache_hit_keeps_original_time_and_expires_after_one_hour():
    """快取 1 小時後過期（假時鐘）；過期前命中快取，顯示原始取得時間。"""
    clock = FakeClock(FIXED_NOW)
    cache = TTLCache(now=clock.monotonic)
    first = await run(text="switch", cache=cache, clock=clock)

    clock.advance(59 * 60)
    hit = await run(text="switch", cache=cache, clock=clock, parser=FakeParser(error=AssertionError("cached")))
    assert hit.from_cache is True
    assert hit.fetched_at == first.fetched_at == FIXED_NOW

    clock.advance(2 * 60)
    parser = FakeParser(switch_item())
    miss = await run(text="switch", cache=cache, clock=clock, parser=parser)
    assert miss.from_cache is False
    assert len(parser.calls) == 1
    assert miss.fetched_at == FIXED_NOW + timedelta(minutes=61)


@pytest.mark.anyio
async def test_fetched_at_is_when_prices_were_fetched():
    clock = FakeClock(FIXED_NOW)
    platforms = anime_platforms(clock, mercari=(4, found(30000.0)), rakuten=(1, found(37000.0)))

    result = await run(text="switch", platforms=platforms, clock=clock)

    assert result.fetched_at == FIXED_NOW + timedelta(seconds=4)


# --- 依價格排序（分潤不影響）---

@pytest.mark.anyio
async def test_platforms_sorted_by_lowest_price_then_unpriced_in_table_order():
    platforms = fake_platforms(
        mercari=found(40000.0),
        rakuten=found(9000.0),
        shopee=failed(FetchStatus.BLOCKED),
    )
    result = await run(text="switch", platforms=platforms)

    assert list(result.platforms) == ["rakuten", "mercari", "yahoo_jp", "shopee", "ruten", "taobao"]


@pytest.mark.anyio
async def test_affiliate_settings_do_not_change_the_order(no_affiliates, monkeypatch):
    from config import settings

    def platforms():
        return fake_platforms(mercari=found(40000.0), rakuten=found(9000.0), shopee=found(1000.0, currency="TWD"))

    without = await run(text="switch", platforms=platforms())
    monkeypatch.setattr(settings, "buyee_affiliate_id", "aff_tag_123")
    monkeypatch.setattr(settings, "shopee_affiliate_base_url", "https://s.shopee.tw/aff?url=")
    with_affiliates = await run(text="switch", platforms=platforms())

    assert "af=aff_tag_123" in with_affiliates.platforms["mercari"].search_url
    assert list(with_affiliates.platforms) == list(without.platforms) == [
        "shopee", "rakuten", "mercari", "yahoo_jp", "ruten", "taobao",
    ]


@pytest.mark.anyio
async def test_results_with_timed_out_platforms_are_not_cached():
    clock = FakeClock(FIXED_NOW)
    cache = TTLCache(now=clock.monotonic)
    platforms = anime_platforms(clock, mercari=(600, found(30000.0)), rakuten=(1, found(37000.0)))

    await run(text="switch", platforms=platforms, cache=cache, clock=clock)

    assert len(cache) == 0
