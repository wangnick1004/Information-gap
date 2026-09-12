import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app import (
    construct_platform_search_url,
    fetch_and_generate_flex_message,
    fetch_lightweight_platform_min_price,
    fetch_lightweight_prices,
    fetch_mercari_min_price,
    fetch_rakuten_min_price,
    filter_extreme_low_prices,
    parse_platform_first_page_prices,
)


def test_filter_extreme_low_prices():
    """Test filtering out extreme low values under 300 JPY."""
    # Discard empty boxes (< 300 JPY)
    raw = [100.0, 200.0, 299.0, 300.0, 1500.0, 4200.0]
    filtered = filter_extreme_low_prices(raw, min_valid_price=300.0)
    assert filtered == [300.0, 1500.0, 4200.0]

    # All items below threshold
    assert filter_extreme_low_prices([50.0, 150.0, 280.0], min_valid_price=300.0) == []

    # Empty list
    assert filter_extreme_low_prices([]) == []


def test_parse_platform_first_page_prices_html_cards():
    """Test parsing HTML with itemCard elements up to 5 items."""
    html = """
    <html>
      <body>
        <div class="itemCard"><span class="price">¥150</span></div>
        <div class="itemCard"><span class="price">¥250</span></div>
        <div class="itemCard"><span class="price">¥3,500</span></div>
        <div class="itemCard"><span class="price">4200円</span></div>
        <div class="itemCard"><span class="price">¥4,800</span></div>
        <div class="itemCard"><span class="price">¥6,000</span></div>
      </body>
    </html>
    """
    prices = parse_platform_first_page_prices(html, platform="mercari", max_items=5)
    assert len(prices) == 5
    assert prices == [150.0, 250.0, 3500.0, 4200.0, 4800.0]


def test_parse_platform_first_page_prices_rakuten_cards():
    """Test parsing Rakuten HTML cards."""
    html = """
    <html>
      <body>
        <div class="searchresultitem"><span class="price--yen">¥2,800</span></div>
        <div class="searchresultitem"><span class="price--yen">¥3,200</span></div>
        <div class="searchresultitem"><span class="price--yen">¥3,600</span></div>
      </body>
    </html>
    """
    prices = parse_platform_first_page_prices(html, platform="rakuten", max_items=5)
    assert prices == [2800.0, 3200.0, 3600.0]


def test_parse_platform_first_page_prices_json():
    """Test parsing raw JSON search response."""
    data = {
        "items": [
            {"name": "Item 1", "price": 1200},
            {"name": "Item 2", "price": 1800},
            {"name": "Item 3", "price": 2400},
        ]
    }
    prices = parse_platform_first_page_prices(json.dumps(data), platform="mercari", max_items=5)
    assert prices == [1200.0, 1800.0, 2400.0]


def test_construct_platform_search_url():
    """Test search URL construction for Mercari and Rakuten."""
    mercari_url = construct_platform_search_url("mercari", "Sony WH-1000XM5")
    assert "https://buyee.jp/mercari/search?keyword=" in mercari_url
    assert "WH-1000XM5" in mercari_url

    rakuten_url = construct_platform_search_url("rakuten", "Switch 2")
    assert "https://buyee.jp/rakuten/shopping/search/category/0?query=" in rakuten_url
    # Alphanumeric spacing condensed for Rakuten
    assert "SWITCH2" in rakuten_url


@pytest.mark.anyio
async def test_fetch_lightweight_platform_min_price_success():
    """
    Test successful lightweight price fetching:
    1. First 5 items: [150, 250, 3500, 4200, 4800]
    2. Filter out items < 300 JPY -> [3500, 4200, 4800]
    3. Minimum valid price: 3500 JPY
    4. Convert to TWD with 1.5% fee: 3500 * 0.21 * 1.015 = 746 TWD
    """
    mock_html = """
    <div class="itemCard"><span class="price">¥150</span></div>
    <div class="itemCard"><span class="price">¥250</span></div>
    <div class="itemCard"><span class="price">¥3,500</span></div>
    <div class="itemCard"><span class="price">¥4,200</span></div>
    <div class="itemCard"><span class="price">¥4,800</span></div>
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html
    mock_client.get = AsyncMock(return_value=mock_resp)

    min_price_twd = await fetch_lightweight_platform_min_price(
        platform="mercari",
        query="Sony WH-1000XM5",
        timeout_seconds=2.0,
        min_valid_jpy=300.0,
        exchange_rate=0.21,
        overseas_fee_rate=0.015,
        client=mock_client,
    )

    # 3500 * 0.21 * 1.015 = 746.025 -> round to 746
    assert min_price_twd == 746


@pytest.mark.anyio
async def test_fetch_lightweight_platform_min_price_all_below_threshold():
    """Test that when all scraped prices are below 300 JPY (empty boxes), returns None."""
    mock_html = """
    <div class="itemCard"><span class="price">¥100</span></div>
    <div class="itemCard"><span class="price">¥200</span></div>
    <div class="itemCard"><span class="price">¥250</span></div>
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html
    mock_client.get = AsyncMock(return_value=mock_resp)

    min_price_twd = await fetch_lightweight_platform_min_price(
        platform="mercari",
        query="空箱",
        timeout_seconds=2.0,
        min_valid_jpy=300.0,
        client=mock_client,
    )
    assert min_price_twd is None


@pytest.mark.anyio
async def test_fetch_lightweight_platform_min_price_timeout_graceful():
    """Test strict 2-second timeout gracefully returns None without crashing."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=asyncio.TimeoutError("Connection timed out"))

    min_price_twd = await fetch_lightweight_platform_min_price(
        platform="rakuten",
        query="Switch 2",
        timeout_seconds=2.0,
        client=mock_client,
    )
    assert min_price_twd is None


@pytest.mark.anyio
async def test_fetch_lightweight_platform_min_price_error_graceful():
    """Test network or HTTP errors gracefully return None without crashing."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_client.get = AsyncMock(return_value=mock_resp)

    min_price_twd = await fetch_lightweight_platform_min_price(
        platform="rakuten",
        query="Nintendo Switch",
        timeout_seconds=2.0,
        client=mock_client,
    )
    assert min_price_twd is None


@pytest.mark.anyio
async def test_fetch_mercari_and_rakuten_convenience_functions():
    """Test fetch_mercari_min_price and fetch_rakuten_min_price wrappers."""
    with patch("services.lightweight_fetcher.fetch_lightweight_platform_min_price", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = 850
        res_m = await fetch_mercari_min_price("Test Mercari")
        assert res_m == 850
        assert mock_fetch.call_args[1]["platform"] == "mercari"

        mock_fetch.return_value = 920
        res_r = await fetch_rakuten_min_price("Test Rakuten")
        assert res_r == 920
        assert mock_fetch.call_args[1]["platform"] == "rakuten"


@pytest.mark.anyio
async def test_fetch_lightweight_prices_concurrent():
    """Test concurrent multi-platform price fetching."""
    with patch("services.lightweight_fetcher.fetch_lightweight_platform_min_price", new_callable=AsyncMock) as mock_fetch:
        async def side_effect(platform, **kwargs):
            if platform == "mercari":
                return 750
            elif platform == "rakuten":
                return 820
            return None

        mock_fetch.side_effect = side_effect
        results = await fetch_lightweight_prices("Test Item", platforms=["mercari", "rakuten"])
        assert results == {"mercari": 750, "rakuten": 820}


@pytest.mark.anyio
async def test_fetch_and_generate_flex_message_replaces_fallback():
    """
    Test that fetch_and_generate_flex_message passes calculated min_price to Flex generator,
    replacing '(點擊查看)' with '(約 NT${min_price})', or keeping '(點擊查看)' on failure.
    """
    # 1. Successful price scenario
    with patch("main.fetch_lightweight_platform_min_price", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = 1250

        flex = await fetch_and_generate_flex_message(
            keyword_ja="Sony WH-1000XM5",
            platform="mercari",
            enable_dynamic_buttons=True,
        )
        card1 = flex["contents"][0]
        buttons = [c for c in card1["footer"]["contents"] if c.get("type") == "button"]
        # Button 0 is Mercari
        assert buttons[0]["action"]["label"] == "Mercari (約 NT$1250)"
        assert buttons[0]["text"] == "Mercari (約 NT$1250)"
        # Button 2 is Rakuten (which was not fetched, so should be fallback)
        assert buttons[2]["action"]["label"] == "日本樂天 (點擊查看)"

    # 2. Timeout / None scenario -> fallback activated
    with patch("main.fetch_lightweight_platform_min_price", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None

        flex_fallback = await fetch_and_generate_flex_message(
            keyword_ja="Sony WH-1000XM5",
            platform="mercari",
            enable_dynamic_buttons=True,
        )
        card1_fallback = flex_fallback["contents"][0]
        buttons_fallback = [c for c in card1_fallback["footer"]["contents"] if c.get("type") == "button"]
        assert buttons_fallback[0]["action"]["label"] == "Mercari (點擊查看)"
        assert buttons_fallback[0]["text"] == "Mercari (點擊查看)"
