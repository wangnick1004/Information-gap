import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app import (
    MERCARI_RAPIDAPI_KEY,
    MERCARI_SCRAPER_URL,
    RAPIDAPI_HOST_MERCARI,
    RAPIDAPI_HOST_RAKUTEN,
    RAPIDAPI_KEY,
    SERPAPI_KEY,
    RateLimitExceededError,
    ThirdPartyAPIError,
    call_mercari_scraper_api,
    call_third_party_api,
    fetch_mercari_api_price,
    fetch_price,
    format_platform_button_component,
    get_mock_plausible_price,
    inject_mercari_button_to_flex,
    is_placeholder_key,
)


def test_configuration_and_constants():
    """Test RapidAPI key, URL, and host configuration."""
    assert is_placeholder_key("YOUR_RAPIDAPI_KEY_HERE") is True
    assert is_placeholder_key("YOUR_SERPAPI_KEY_HERE") is True
    assert is_placeholder_key("") is True
    assert is_placeholder_key(None) is True
    assert is_placeholder_key("live_api_key_abc123") is False

    assert MERCARI_SCRAPER_URL == "https://mercari-japan-ultimate-scraper.p.rapidapi.com/mercari/search"
    assert RAPIDAPI_HOST_MERCARI == "mercari-japan-ultimate-scraper.p.rapidapi.com"
    assert MERCARI_RAPIDAPI_KEY == "a9f0474e1dmsh9c56716a5c32a97p19fc3ejsn7ad627df9796"
    assert is_placeholder_key(MERCARI_RAPIDAPI_KEY) is False
    assert is_placeholder_key(RAPIDAPI_KEY) is False or isinstance(RAPIDAPI_KEY, str)


@pytest.mark.anyio
async def test_mercari_async_post_request_and_payload():
    """
    Test Step 1: Setup async POST request with URL, headers, and keyword payload.
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "items": [
            {"name": "Box only", "price": 300},        # < 500 JPY filtered out
            {"name": "Accessory", "price": 450},       # < 500 JPY filtered out
            {"name": "Pokemon Item A", "price": 1000}, # Valid min price
            {"name": "Pokemon Item B", "price": 2500},
            {"name": "Pokemon Item C", "price": 3000},
        ]
    })
    mock_client.post = AsyncMock(return_value=mock_resp)

    jp_keyword = "ポケモン モンコレ"
    twd_price = await call_mercari_scraper_api(
        jp_keyword=jp_keyword,
        timeout_seconds=2.5,
        client=mock_client,
    )

    # Verify POST request parameters
    mock_client.post.assert_called_once()
    called_url = mock_client.post.call_args[0][0]
    called_kwargs = mock_client.post.call_args[1]

    assert called_url == "https://mercari-japan-ultimate-scraper.p.rapidapi.com/mercari/search"
    assert called_kwargs["headers"]["Content-Type"] == "application/json"
    assert called_kwargs["headers"]["x-rapidapi-host"] == "mercari-japan-ultimate-scraper.p.rapidapi.com"
    assert called_kwargs["headers"]["x-rapidapi-key"] == "a9f0474e1dmsh9c56716a5c32a97p19fc3ejsn7ad627df9796"
    assert called_kwargs["json"] == {"keyword": jp_keyword}

    # Verify Currency Conversion (Step 3):
    # min valid price = 1000 JPY -> 1000 * 0.21 * 1.015 = 213.15 -> 213 TWD
    assert twd_price == 213


@pytest.mark.anyio
async def test_data_extraction_and_cleaning_filters_under_500_jpy():
    """
    Test Step 2: Data Extraction & Cleaning:
    - Extracts first 5 to 10 items
    - Filters out extreme low values (< 500 JPY)
    - Finds the minimum valid price
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "data": [
            {"title": "Empty Box", "itemPrice": "¥200"},
            {"title": "Manual Leaflet", "itemPrice": "499"},
            {"title": "Valid Console", "itemPrice": "15,000"},
            {"title": "Valid Game", "itemPrice": "2,000"},
            {"title": "Valid Controller", "itemPrice": "3,500"},
        ]
    })
    mock_client.post = AsyncMock(return_value=mock_resp)

    # Valid prices are: 15000, 2000, 3500. Min valid price is 2000 JPY.
    # 2000 * 0.21 * 1.015 = 426.3 -> 426 TWD
    price = await fetch_mercari_api_price("ニンテンドー スイッチ", client=mock_client)
    assert price == 426


@pytest.mark.anyio
async def test_data_extraction_all_under_500_jpy_returns_none():
    """Test that if all extracted items are under 500 JPY, it returns None."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "items": [
            {"price": 100},
            {"price": 200},
            {"price": 300},
            {"price": 450},
        ]
    })
    mock_client.post = AsyncMock(return_value=mock_resp)

    price = await fetch_mercari_api_price("junk item", client=mock_client)
    assert price is None


@pytest.mark.anyio
async def test_mercari_api_rate_limit_exceeded_fallback():
    """Test HTTP 429 rate limit raises or returns None gracefully."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = json.dumps({"message": "You have exceeded the RATE limit. (Too Many Requests)"})
    mock_client.post = AsyncMock(return_value=mock_resp)

    price = await fetch_mercari_api_price("Sony WH-1000XM5", client=mock_client)
    assert price is None


@pytest.mark.anyio
async def test_mercari_api_network_timeout_fallback():
    """Test strict 2.5s network timeout returns None gracefully."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=asyncio.TimeoutError("Mercari API timed out after 2.5s"))

    price = await fetch_mercari_api_price("Sony WH-1000XM5", client=mock_client, timeout_seconds=2.5)
    assert price is None


@pytest.mark.anyio
async def test_mercari_api_server_error_fallback():
    """Test HTTP 500 / 503 returns None gracefully."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_client.post = AsyncMock(return_value=mock_resp)

    price = await fetch_mercari_api_price("Sony WH-1000XM5", client=mock_client)
    assert price is None


def test_ui_integration_format_platform_button_component():
    """
    Test Step 4: UI Integration:
    - Button with valid price reads: 'Mercari (約 NT${twd_price}起)'
    - Button with None/0 reads: 'Mercari (點擊查看)'
    """
    # Valid price with default "起" suffix for Mercari
    btn_with_price = format_platform_button_component(
        platform_name="Mercari",
        price=213,
        target_url="https://buyee.jp/mercari/search?keyword=pokemon",
        color="#E60012",
    )
    assert btn_with_price["type"] == "button"
    assert btn_with_price["text"] == "Mercari (約 NT$213起)"
    assert btn_with_price["action"]["label"] == "Mercari (約 NT$213起)"
    assert btn_with_price["action"]["uri"] == "https://buyee.jp/mercari/search?keyword=pokemon"
    assert btn_with_price["color"] == "#E60012"

    # Fallback when price is None
    btn_fallback = format_platform_button_component(
        platform_name="Mercari",
        price=None,
        target_url="https://buyee.jp/mercari/search?keyword=pokemon",
    )
    assert btn_fallback["text"] == "Mercari (點擊查看)"
    assert btn_fallback["action"]["label"] == "Mercari (點擊查看)"

    # Fallback when price is 0
    btn_zero = format_platform_button_component(
        platform_name="Mercari",
        price=0,
        target_url="https://buyee.jp/mercari/search?keyword=pokemon",
    )
    assert btn_zero["text"] == "Mercari (點擊查看)"
    assert btn_zero["action"]["label"] == "Mercari (點擊查看)"


def test_ui_integration_inject_mercari_button_to_flex():
    """
    Test Step 4: Inject into Flex Message payload:
    - Replaces button text with 'Mercari (約 NT${twd_price}起)'
    - Falls back to 'Mercari (點擊查看)' on None/failure
    """
    flex_sample = {
        "type": "carousel",
        "contents": [
            {
                "type": "bubble",
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "button",
                            "text": "Mercari (點擊查看)",
                            "action": {
                                "type": "uri",
                                "label": "Mercari (點擊查看)",
                                "uri": "https://buyee.jp/mercari/search",
                            },
                        },
                        {
                            "type": "button",
                            "text": "日本樂天 (點擊查看)",
                            "action": {
                                "type": "uri",
                                "label": "日本樂天 (點擊查看)",
                                "uri": "https://buyee.jp/rakuten",
                            },
                        },
                    ],
                },
            }
        ],
    }

    # Inject price 1500 -> "Mercari (約 NT$1500起)"
    updated = inject_mercari_button_to_flex(flex_sample, 1500)
    mercari_btn = updated["contents"][0]["footer"]["contents"][0]
    assert mercari_btn["text"] == "Mercari (約 NT$1500起)"
    assert mercari_btn["action"]["label"] == "Mercari (約 NT$1500起)"

    # Rakuten button remains unchanged
    rakuten_btn = updated["contents"][0]["footer"]["contents"][1]
    assert rakuten_btn["text"] == "日本樂天 (點擊查看)"

    # Inject None -> fallback "Mercari (點擊查看)"
    updated_fallback = inject_mercari_button_to_flex(flex_sample, None)
    mercari_btn_fb = updated_fallback["contents"][0]["footer"]["contents"][0]
    assert mercari_btn_fb["text"] == "Mercari (點擊查看)"
    assert mercari_btn_fb["action"]["label"] == "Mercari (點擊查看)"


@pytest.mark.anyio
async def test_fetch_price_mock_fallback():
    """Test mock fallback returns plausible price when enable_mock=True."""
    with patch("price_fetcher.RAPIDAPI_KEY", "YOUR_RAPIDAPI_KEY_HERE"):
        price_mercari = await fetch_price("mercari", "Sony WH-1000XM5", enable_mock=True)
        assert isinstance(price_mercari, int)
        assert price_mercari == 1500

    # Empty keyword returns None
    assert await fetch_price("mercari", "") is None
    assert await fetch_price("mercari", "   ") is None
