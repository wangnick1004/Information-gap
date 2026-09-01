import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.scraper import (
    ScrapedListing,
    ScrapingBlockedError,
    ScrapingError,
    ScrapingResult,
    ScrapingTimeoutError,
    extract_price_number,
    normalize_rakuten_search_keyword,
    normalize_search_keyword,
    parse_buyee_html,
    scrape_buyee_prices,
)

SAMPLE_BUYEE_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head><title>Mercari Search Results - Buyee</title></head>
<body>
<div class="search-result">
    <ul class="item-list">
        <li class="itemCard">
            <div class="itemCard__image">
                <img src="https://static.mercdn.net/item/detail/orig/photos/m10001_1.jpg" alt="ハイキュー 影山 もちマス 2020">
            </div>
            <div class="itemCard__itemName">ハイキュー 影山 もちマス 2020</div>
            <div class="itemCard__price">¥ 1,200</div>
        </li>
        <li class="itemCard">
            <div class="itemCard__image">
                <img data-src="https://static.mercdn.net/item/detail/orig/photos/m10002_1.jpg" alt="影山飛雄 もちもちマスコット">
            </div>
            <div class="itemCard__itemName">影山飛雄 もちもちマスコット</div>
            <div class="itemCard__price">1,500円</div>
        </li>
        <li class="itemCard">
            <div class="itemCard__image">
                <img src="//static.mercdn.net/item/detail/orig/photos/m10003_1.jpg" alt="ハイキュー ぬいぐるみ 影山">
            </div>
            <div class="itemCard__itemName">ハイキュー ぬいぐるみ 影山</div>
            <div class="itemCard__price">¥ 1,800</div>
        </li>
        <li class="itemCard">
            <div class="itemCard__image">
                <img src="https://static.mercdn.net/item/detail/orig/photos/m10004_1.jpg" alt="もちマス 影山">
            </div>
            <div class="itemCard__itemName">もちマス 影山</div>
            <div class="itemCard__price">¥ 2,000</div>
        </li>
        <li class="itemCard">
            <div class="itemCard__image">
                <img src="https://static.mercdn.net/item/detail/orig/photos/m10005_1.jpg" alt="影山 2020 ぬいぐるみ">
            </div>
            <div class="itemCard__itemName">影山 2020 ぬいぐるみ</div>
            <div class="itemCard__price">¥ 2,500</div>
        </li>
        <li class="itemCard">
            <div class="itemCard__image">
                <img src="https://static.mercdn.net/item/detail/orig/photos/m10006_1.jpg" alt="Sixth Item (Should be ignored by top 5)">
            </div>
            <div class="itemCard__itemName">Sixth Item</div>
            <div class="itemCard__price">¥ 9,999</div>
        </li>
    </ul>
</div>
</body>
</html>
"""

NO_RESULTS_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="search-empty">
    <p>該当する商品は見つかりませんでした。</p>
</div>
</body>
</html>
"""


def test_normalize_search_keyword():
    """Test keyword normalization with uppercase English, continuous whitespace, and full-width Japanese spaces."""
    assert normalize_search_keyword("  sony   wh-1000xm5\u3000\u3000ヘッドホン  ") == "SONY WH-1000XM5 ヘッドホン"
    assert normalize_search_keyword("ハイキュー!!\u3000影山飛雄\t\tもちマス 2020") == "ハイキュー!! 影山飛雄 もちマス 2020"
    assert normalize_search_keyword("nike   air jordan 1   chicago") == "NIKE AIR JORDAN 1 CHICAGO"
    assert normalize_search_keyword("") == ""
    assert normalize_search_keyword("   \u3000\t  ") == ""
    assert normalize_search_keyword(None) == ""


def test_extract_price_number():
    """Test price regex extraction across various Japanese price formatting styles."""
    assert extract_price_number("¥ 1,200") == 1200.0
    assert extract_price_number("1,500円") == 1500.0
    assert extract_price_number("¥2,000 JPY") == 2000.0
    assert extract_price_number("500") == 500.0
    assert extract_price_number("invalid") is None
    assert extract_price_number("") is None


def test_parse_buyee_html():
    """Test HTML parsing extracts top 5 listings with prices and clean image URLs."""
    listings = parse_buyee_html(SAMPLE_BUYEE_HTML, max_items=5)
    assert len(listings) == 5
    assert listings[0].price_jpy == 1200.0
    assert listings[0].image_url == "https://static.mercdn.net/item/detail/orig/photos/m10001_1.jpg"
    assert listings[1].price_jpy == 1500.0
    assert listings[1].image_url == "https://static.mercdn.net/item/detail/orig/photos/m10002_1.jpg"
    # Verify protocol-relative URL '//static...' is normalized to 'https://'
    assert listings[2].image_url == "https://static.mercdn.net/item/detail/orig/photos/m10003_1.jpg"


@pytest.mark.anyio
@pytest.mark.anyio
async def test_scrape_buyee_prices_success():
    """Test asynchronous scraping with mocked HTTP response."""
    with patch("services.scraper.fetch_network_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (200, SAMPLE_BUYEE_HTML)

        result = await scrape_buyee_prices("ハイキュー 影山 もちマス 2020", timeout_seconds=10.0)

        assert isinstance(result, ScrapingResult)
        assert result.query == "ハイキュー 影山 もちマス 2020"
        assert "buyee.jp/mercari/search" in result.search_url
        assert result.total_found == 5
        assert result.lowest_price_jpy == 1200.0
        # Median of [1200, 1500, 1800, 2000, 2500] is 1800.0
        assert result.median_price_jpy == 1800.0
        assert result.representative_image_url == "https://static.mercdn.net/item/detail/orig/photos/m10001_1.jpg"
        assert result.sample_prices == [1200.0, 1500.0, 1800.0, 2000.0, 2500.0]


@pytest.mark.anyio
async def test_scrape_buyee_prices_no_results():
    """Test that zero search results triggers ScrapingError."""
    with patch("services.scraper.fetch_network_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (200, NO_RESULTS_HTML)

        with pytest.raises(ScrapingError) as exc_info:
            await scrape_buyee_prices("nonexistent_item_query_123")

        assert "未找到" in str(exc_info.value) or "No listings" in str(exc_info.value)


@pytest.mark.anyio
async def test_scrape_buyee_prices_http_error():
    """Test that 403 HTTP status triggers ScrapingBlockedError."""
    with patch("services.scraper.fetch_network_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (403, "Access Denied")

        with pytest.raises(ScrapingBlockedError) as exc_info:
            await scrape_buyee_prices("query")

        assert "403" in str(exc_info.value)


@pytest.mark.anyio
async def test_scrape_buyee_prices_timeout():
    """Test that network timeouts trigger ScrapingTimeoutError gracefully."""
    import asyncio
    with patch("services.scraper.fetch_network_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = asyncio.TimeoutError("Request timed out")

        with pytest.raises(ScrapingTimeoutError) as exc_info:
            await scrape_buyee_prices("query", timeout_seconds=10.0)

        assert "逾時" in str(exc_info.value) or "timed out" in str(exc_info.value)


@pytest.mark.anyio
async def test_scrape_buyee_prices_empty_query():
    """Test that empty query string raises ScrapingError before sending request."""
    with pytest.raises(ScrapingError) as exc_info:
        await scrape_buyee_prices("   ")
    assert "Search query cannot be empty" in str(exc_info.value)


def test_normalize_rakuten_search_keyword():
    """Test alphanumeric space removal specifically for Rakuten Japan keywords."""
    assert normalize_rakuten_search_keyword("Switch 2") == "SWITCH2"
    assert normalize_rakuten_search_keyword("PS 5") == "PS5"
    assert normalize_rakuten_search_keyword("iPhone 16 Pro") == "IPHONE16 PRO"
    assert normalize_rakuten_search_keyword("Sony WH-1000XM5") == "SONY WH-1000XM5"
    assert normalize_rakuten_search_keyword("Nintendo Switch 2 本體") == "NINTENDO SWITCH2 本體"
    assert normalize_rakuten_search_keyword("") == ""
    assert normalize_rakuten_search_keyword(None) == ""


@pytest.mark.anyio
async def test_search_all_platforms_concurrently():
    """Test that search_all_platforms_concurrently searches JP, TW, and CN platforms simultaneously."""
    from services.scraper import search_all_platforms_concurrently, ScrapingResult
    from services.cache import search_cache

    search_cache.clear()

    mock_html = """
    <html>
      <body>
        <div class="itemCard">
          <span class="itemCard__price">¥35,000</span>
          <img data-src="https://example.com/switch2.jpg" />
          <span class="itemCard__title">Nintendo Switch 2</span>
        </div>
      </body>
    </html>
    """
    with patch("services.scraper.fetch_network_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (200, mock_html)

        res = await search_all_platforms_concurrently(
            query_ja="Switch 2",
            query_zh="Switch 2",
        )

        assert res.query_jp == "Switch 2"
        assert res.query_zh == "Switch 2"
        assert res.japanese_result is not None
        assert res.japanese_result.status == "success"
        assert res.japanese_result.scraping_result.lowest_price_jpy == 35000.0
        assert res.taiwanese_result is not None
        assert res.taiwanese_result.status == "success"
        assert "tw.buy.yahoo.com" in res.taiwanese_result.search_url
        assert res.chinese_result is not None
        assert res.chinese_result.status == "success"
        assert "world.taobao.com" in res.chinese_result.search_url
        assert res.primary_scraping_result is not None


def test_parse_buyee_raw_json():
    """Test ultra-fast raw JSON response parsing without DOM overhead."""
    from services.scraper import parse_buyee_json_or_html

    raw_json = json.dumps({
        "items": [
            {
                "name": "Sony WH-1000XM5 Black",
                "price": 28000,
                "imageUrl": "https://example.com/wh1000xm5.jpg",
            },
            {
                "name": "Sony WH-1000XM5 Silver",
                "price": 29000,
                "imageUrl": "https://example.com/wh1000xm5_silver.jpg",
            }
        ]
    })

    listings = parse_buyee_json_or_html(raw_json)
    assert len(listings) == 2
    assert listings[0].price_jpy == 28000.0
    assert listings[0].title == "Sony WH-1000XM5 Black"
    assert listings[0].image_url == "https://example.com/wh1000xm5.jpg"


def test_parse_buyee_nextjs_embedded_json():
    """Test fast parsing from __NEXT_DATA__ JSON script tag."""
    from services.scraper import parse_buyee_json_or_html

    html_with_next_data = """
    <html>
      <head>
        <script id="__NEXT_DATA__" type="application/json">
        {
          "props": {
            "pageProps": {
              "items": [
                {
                  "name": "Nikon Zfc Body",
                  "price": 72000,
                  "imageUrl": "https://example.com/zfc.jpg"
                }
              ]
            }
          }
        }
        </script>
      </head>
      <body><div>Other HTML</div></body>
    </html>
    """

    listings = parse_buyee_json_or_html(html_with_next_data)
    assert len(listings) == 1
    assert listings[0].price_jpy == 72000.0
    assert listings[0].title == "Nikon Zfc Body"
    assert listings[0].image_url == "https://example.com/zfc.jpg"

