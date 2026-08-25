from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.scraper import (
    ScrapedListing,
    ScrapingError,
    ScrapingResult,
    extract_price_number,
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
async def test_scrape_buyee_prices_success():
    """Test asynchronous scraping with mocked HTTP response."""
    mock_response = httpx.Response(
        status_code=200,
        text=SAMPLE_BUYEE_HTML,
        request=httpx.Request("GET", "https://buyee.jp/mercari/search"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await scrape_buyee_prices("ハイキュー 影山 もちマス 2020")

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
    mock_response = httpx.Response(
        status_code=200,
        text=NO_RESULTS_HTML,
        request=httpx.Request("GET", "https://buyee.jp/mercari/search"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(ScrapingError) as exc_info:
            await scrape_buyee_prices("nonexistent_item_query_123")

        assert "No listings found" in str(exc_info.value)


@pytest.mark.anyio
async def test_scrape_buyee_prices_http_error():
    """Test that non-200 HTTP status triggers ScrapingError."""
    mock_response = httpx.Response(
        status_code=403,
        text="Access Denied",
        request=httpx.Request("GET", "https://buyee.jp/mercari/search"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(ScrapingError) as exc_info:
            await scrape_buyee_prices("query")

        assert "status code 403" in str(exc_info.value)


@pytest.mark.anyio
async def test_scrape_buyee_prices_timeout():
    """Test that network timeouts trigger ScrapingError gracefully."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.ReadTimeout("Request timed out")

        with pytest.raises(ScrapingError) as exc_info:
            await scrape_buyee_prices("query")

        assert "timed out" in str(exc_info.value)


@pytest.mark.anyio
async def test_scrape_buyee_prices_empty_query():
    """Test that empty query string raises ScrapingError before sending request."""
    with pytest.raises(ScrapingError) as exc_info:
        await scrape_buyee_prices("   ")
    assert "Search query cannot be empty" in str(exc_info.value)
