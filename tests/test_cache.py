import time
import pytest
from services.cache import TTLCache


def test_ttl_cache_basic_get_set():
    cache = TTLCache(default_ttl=10.0, max_size=5)
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"
    assert "key1" in cache
    assert len(cache) == 1


def test_ttl_cache_expiration():
    # Cache with very short TTL
    cache = TTLCache(default_ttl=0.1, max_size=5)
    cache.set("short_lived", {"data": 123}, ttl=0.05)
    assert cache.get("short_lived") == {"data": 123}

    time.sleep(0.06)
    assert cache.get("short_lived") is None
    assert "short_lived" not in cache


def test_ttl_cache_max_size_eviction():
    cache = TTLCache(default_ttl=60.0, max_size=3)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")
    assert len(cache) == 3

    # Adding 4th item should evict oldest
    cache.set("k4", "v4")
    assert len(cache) <= 3
    assert cache.get("k4") == "v4"


def test_ttl_cache_delete_and_clear():
    cache = TTLCache(default_ttl=60.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    assert len(cache) == 2

    cache.delete("k1")
    assert cache.get("k1") is None
    assert cache.get("k2") == "v2"

    cache.clear()
    assert len(cache) == 0
    assert cache.get("k2") is None


@pytest.mark.anyio
async def test_prewarm_search_cache():
    """Test background cache pre-warming makes hot keywords answer from cache."""
    from unittest.mock import patch
    from main import PREWARM_KEYWORDS, prewarm_search_cache
    from services.cache import search_cache
    from services.comparison import compare_prices
    from services.parser import ParsedItem
    from tests.fakes import FakeParser, buyee_result, fake_fetchers, pipeline_with, returning

    search_cache.clear()
    parser = FakeParser(ParsedItem(keyword_zh="Switch 2", keyword_jp="Switch 2"))
    fetchers = fake_fetchers(buyee=returning(buyee_result()))

    with patch("main.compare_prices", pipeline_with(parser, fetchers)):
        await prewarm_search_cache()

    assert len(parser.calls) == len(PREWARM_KEYWORDS)
    # 使用者之後查同一個熱門關鍵字時直接命中快取，不再呼叫 AI
    cached = await compare_prices(text="switch 2", parser=FakeParser(error=AssertionError("AI called")))
    assert cached.from_cache is True


@pytest.mark.anyio
async def test_prewarm_does_not_cache_failed_comparisons():
    """啟動預熱時抓價失敗，不應快取僅連結的卡片。"""
    from unittest.mock import patch
    from main import prewarm_search_cache
    from services.cache import search_cache
    from services.parser import ParsedItem
    from services.scraper import ScrapingTimeoutError
    from tests.fakes import FakeParser, fake_fetchers, pipeline_with, returning

    search_cache.clear()
    fetchers = fake_fetchers(buyee=returning(ScrapingTimeoutError("slow")))

    with patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="x")), fetchers)):
        await prewarm_search_cache()

    assert len(search_cache) == 0
