import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app import (
    FASHION_RESALE_API_URL,
    RAPIDAPI_HOST_FASHION_RESALE,
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

    assert FASHION_RESALE_API_URL == "https://fashion-resale-api.p.rapidapi.com/search"
    assert RAPIDAPI_HOST_FASHION_RESALE == "fashion-resale-api.p.rapidapi.com"
    assert RAPIDAPI_HOST_MERCARI == "fashion-resale-api.p.rapidapi.com"


@pytest.mark.anyio
async def test_fashion_resale_mercari_platform_filter_and_params():
    """
    Test Step 1: Platform Filter:
    - Querystring must strictly include {"q": keyword, "platform": "mercari"}
    - Headers: x-rapidapi-host and x-rapidapi-key from environment
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Butterfly Viscaria FL", "price": 18000, "platform": "mercari"},
            {"title": "Butterfly Viscaria ST", "price": 20000, "platform": "mercari"},
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    test_env_key = "test_rapidapi_key_env_12345"
    with patch.dict(os.environ, {"RAPIDAPI_KEY": test_env_key}):
        twd_price = await call_mercari_scraper_api(
            jp_keyword="ビスカリア",
            timeout_seconds=2.5,
            client=mock_client,
        )

    # Verify GET request parameters and querystring
    mock_client.get.assert_called_once()
    called_url = mock_client.get.call_args[0][0]
    called_kwargs = mock_client.get.call_args[1]

    assert called_url == "https://fashion-resale-api.p.rapidapi.com/search"
    assert called_kwargs["headers"]["x-rapidapi-host"] == "fashion-resale-api.p.rapidapi.com"
    assert called_kwargs["headers"]["x-rapidapi-key"] == test_env_key
    assert called_kwargs["params"] == {"q": "ビスカリア", "platform": "mercari"}

    # Verify Currency Conversion:
    # 18000 * 0.21 * 1.015 = 3836.7 -> 3837 TWD
    assert twd_price == 3837


@pytest.mark.anyio
async def test_data_parsing_filters_under_2500_and_finds_minimum():
    """
    Test Step 2: Data Parsing & Filtering:
    - Collects ALL valid numeric prices from the 'listings' array into a list
    - Discards any price < 2500 JPY (edge tapes, rubber protectors, empty boxes)
    - Finds the minimum price from the filtered list (e.g., 15000 from [500, 1200, 18000, 15000, 22000])
    - Converts to TWD (15000 * 0.21 * 1.015 = 3197.25 -> 3197 TWD)
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Edge Tape", "price": "¥500"},           # < 2500 JPY discarded
            {"title": "Rubber Protector", "price": 1200},      # < 2500 JPY discarded
            {"title": "Empty Box", "price": "2,400"},          # < 2500 JPY discarded
            {"title": "Viscaria Racket A", "price": "18,000"}, # valid
            {"title": "Viscaria Racket B", "price": 15000},    # valid minimum!
            {"title": "Viscaria Racket C", "price": "22,000"}, # valid
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        # 15000 * 0.21 * 1.015 = 3197.25 -> 3197 TWD
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client)
        assert price == 3197


@pytest.mark.anyio
async def test_data_parsing_all_under_2500_returns_none():
    """
    Test that if all extracted items are under 2500 JPY, it returns None.
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Edge Tape", "price": 500},
            {"title": "Rubber Protector", "price": 1200},
            {"title": "Clean Sponge", "price": 800},
            {"title": "Empty Box", "price": 2499},
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client)
        assert price is None


@pytest.mark.anyio
async def test_security_first_no_hardcoded_key_fails_when_env_empty():
    """
    Test Step 3: Security First:
    When RAPIDAPI_KEY is not configured in os.environ or is placeholder,
    it must NOT use any hardcoded fallback key and gracefully return None.
    """
    with patch.dict(os.environ, {"RAPIDAPI_KEY": ""}, clear=True), \
         patch("price_fetcher.RAPIDAPI_KEY", ""):
        price = await fetch_mercari_api_price("ビスカリア", enable_mock=False)
        assert price is None


@pytest.mark.anyio
async def test_data_parsing_empty_listings_returns_none():
    """Test that empty listings array returns None gracefully."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"listings": []})
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("nonexistent item", client=mock_client)
        assert price is None


@pytest.mark.anyio
async def test_mercari_api_rate_limit_exceeded_fallback():
    """Test HTTP 429 rate limit returns None gracefully."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = json.dumps({"message": "You have exceeded the RATE limit. (Too Many Requests)"})
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client)
        assert price is None


@pytest.mark.anyio
async def test_mercari_api_network_timeout_fallback():
    """Test strict network timeout returns None gracefully."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=asyncio.TimeoutError("Fashion Resale API timed out"))

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client, timeout_seconds=2.5)
        assert price is None


@pytest.mark.anyio
async def test_mercari_api_server_error_fallback():
    """Test HTTP 500 / 503 returns None gracefully."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client)
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
        price=3837,
        target_url="https://buyee.jp/mercari/search?keyword=viscaria",
        color="#E60012",
    )
    assert btn_with_price["type"] == "button"
    assert btn_with_price["text"] == "Mercari (約 NT$3837起)"
    assert btn_with_price["action"]["label"] == "Mercari (約 NT$3837起)"
    assert btn_with_price["action"]["uri"] == "https://buyee.jp/mercari/search?keyword=viscaria"
    assert btn_with_price["color"] == "#E60012"

    # Fallback when price is None
    btn_fallback = format_platform_button_component(
        platform_name="Mercari",
        price=None,
        target_url="https://buyee.jp/mercari/search?keyword=viscaria",
    )
    assert btn_fallback["text"] == "Mercari (點擊查看)"
    assert btn_fallback["action"]["label"] == "Mercari (點擊查看)"

    # Fallback when price is 0
    btn_zero = format_platform_button_component(
        platform_name="Mercari",
        price=0,
        target_url="https://buyee.jp/mercari/search?keyword=viscaria",
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

    # Inject price 3837 -> "Mercari (約 NT$3837起)"
    updated = inject_mercari_button_to_flex(flex_sample, 3837)
    mercari_btn = updated["contents"][0]["footer"]["contents"][0]
    assert mercari_btn["text"] == "Mercari (約 NT$3837起)"
    assert mercari_btn["action"]["label"] == "Mercari (約 NT$3837起)"

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
    with patch.dict(os.environ, {"RAPIDAPI_KEY": ""}, clear=True), \
         patch("price_fetcher.RAPIDAPI_KEY", ""):
        price_mercari = await fetch_price("mercari", "Sony WH-1000XM5", enable_mock=True)
        assert isinstance(price_mercari, int)
        assert price_mercari == 1500

    # Empty keyword returns None
    assert await fetch_price("mercari", "") is None
    assert await fetch_price("mercari", "   ") is None
