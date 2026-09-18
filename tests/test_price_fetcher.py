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
            {"title": "Butterfly Viscaria FL", "price": 80, "platform": "mercari"},
            {"title": "Butterfly Viscaria ST", "price": 100, "platform": "mercari"},
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    test_env_key = "test_rapidapi_key_env_12345"
    with patch.dict(os.environ, {"RAPIDAPI_KEY": test_env_key}):
        twd_price = await call_mercari_scraper_api(
            jp_keyword="ビスカリア",
            timeout_seconds=8.0,
            client=mock_client,
            estimated_min_usd=100,
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
    # 80 USD * 32.5 * 1.015 = 2639 TWD
    assert twd_price == 2639


@pytest.mark.anyio
async def test_data_parsing_dynamic_threshold_filters_accessories_and_finds_minimum():
    """
    Test Step 2: Data Parsing & Dynamic Filtering:
    - Collects ALL valid numeric USD prices from the 'listings' array into a list
    - Filters out prices < (estimated_min_usd * 0.6) (edge tapes, rubber protectors, empty boxes)
    - Finds the minimum price from the filtered list (e.g., 50 from [5, 12, 24, 70, 50, 85] with estimated_min_usd=60)
    - Converts USD to TWD using standard rate (50 * 32.5 * 1.015 = 1649.375 -> 1649 TWD)
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Edge Tape", "price": "$5"},              # < 36 USD discarded
            {"title": "Rubber Protector", "price": 12},         # < 36 USD discarded
            {"title": "Empty Box", "price": "24"},              # < 36 USD discarded
            {"title": "Viscaria Racket A", "price": "70"},      # >= 36 USD valid
            {"title": "Viscaria Racket B", "price": 50},        # >= 36 USD valid minimum!
            {"title": "Viscaria Racket C", "price": "85"},      # >= 36 USD valid
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        # estimated_min_usd=60 -> threshold is 36 USD. Valid prices: 70, 50, 85 -> min is 50
        # 50 * 32.5 * 1.015 = 1649.375 -> 1649 TWD
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client, estimated_min_usd=60)
        assert price == 1649


@pytest.mark.anyio
async def test_data_parsing_all_below_dynamic_threshold_returns_none(caplog):
    """
    Test that if all extracted items are below (estimated_min_usd * 0.6), it returns None
    and logs total items, raw prices, and the filter empty warning with threshold.
    """
    import logging
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Edge Tape", "price": 5},
            {"title": "Rubber Protector", "price": 12},
            {"title": "Clean Sponge", "price": 8},
            {"title": "Empty Box", "price": 24},
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    with caplog.at_level(logging.INFO):
        with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
            # estimated_min_usd=60 -> threshold is 36 USD
            price = await fetch_mercari_api_price("ビスカリア", client=mock_client, estimated_min_usd=60)
            assert price is None

    assert "📊 [Mercari Listings] Total items fetched: 4" in caplog.text
    assert "💰 [Mercari Raw Prices (USD)] Surviving prices after title filter: [5.0, 12.0, 8.0, 24.0]" in caplog.text
    assert "⚠️ [Filter Empty] All Mercari items were below cutoffs" in caplog.text


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
async def test_mercari_api_network_timeout_fallback(caplog):
    """Test strict network timeout returns None gracefully and logs clear timeout warning."""
    import logging
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=asyncio.TimeoutError())

    with caplog.at_level(logging.WARNING):
        with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
            price = await fetch_mercari_api_price("ビスカリア", client=mock_client, timeout_seconds=8.0)
            assert price is None

    assert "⚠️ [Timeout] Third-Party API request exceeded limit." in caplog.text


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


def test_default_timeout_signatures():
    """Verify default timeout_seconds is 8.0 and estimated_min_usd is supported for fetch_mercari_api_price and fetch_price."""
    import inspect
    sig_mercari = inspect.signature(fetch_mercari_api_price)
    assert sig_mercari.parameters["timeout_seconds"].default == 8.0
    assert "estimated_min_usd" in sig_mercari.parameters
    assert sig_mercari.parameters["estimated_min_usd"].default is None

    sig_fetch = inspect.signature(fetch_price)
    assert sig_fetch.parameters["timeout_seconds"].default == 8.0
    assert "estimated_min_usd" in sig_fetch.parameters
    assert sig_fetch.parameters["estimated_min_usd"].default is None


@pytest.mark.anyio
async def test_data_parsing_genuine_bargain_accepted():
    """
    Verify that genuine bargains (e.g. 40% margin allowance) are accepted
    while cheaper accessories are killed:
    - estimated_min_usd = 100 -> threshold = 100 * 0.6 = 60 USD
    - item at $65 USD (genuine super-bargain) >= $60 -> ACCEPTED
    - item at $25 USD (cheap accessory/case) < $60 -> KILLED
    - 65 * 32.5 * 1.015 = 2144.1875 -> 2144 TWD
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Cheap Case/Accessory", "price": 25},
            {"title": "Genuine Super-Bargain Main Item", "price": 65},
            {"title": "Regular Price Item", "price": 110},
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("ビスカリア", client=mock_client, estimated_min_usd=100)
        assert price == 2144


def test_base_blacklist_definition():
    """Verify BASE_BLACKLIST contains exact specified keywords."""
    from services.price_fetcher import BASE_BLACKLIST
    expected = ["ケース", "カバー", "フィルム", "空箱", "ジャンク", "保護", "パーツ", "部品", "用", "box only"]
    assert BASE_BLACKLIST == expected


def test_get_active_blacklist_exemption_mechanism():
    """Test dynamic active blacklist properly exempts terms present in jp_keyword (case-insensitive)."""
    from services.price_fetcher import BASE_BLACKLIST, get_active_blacklist

    # 1. No blacklist word in keyword -> full blacklist active
    active1 = get_active_blacklist("Nintendo Switch 本体")
    assert active1 == BASE_BLACKLIST

    # 2. "ケース" in keyword -> "ケース" exempted, 9 terms remain
    active2 = get_active_blacklist("Switch ケース")
    assert "ケース" not in active2
    assert "カバー" in active2
    assert len(active2) == len(BASE_BLACKLIST) - 1

    # 3. Case-insensitive check: "BOX ONLY" in keyword -> "box only" exempted
    active3 = get_active_blacklist("Pokemon Card BOX ONLY")
    assert "box only" not in active3
    assert len(active3) == len(BASE_BLACKLIST) - 1

    # 4. Multiple terms: "Switch用 ケース" -> both "用" and "ケース" exempted
    active4 = get_active_blacklist("Switch用 ケース")
    assert "ケース" not in active4
    assert "用" not in active4
    assert "カバー" in active4
    assert len(active4) == len(BASE_BLACKLIST) - 2


@pytest.mark.anyio
async def test_semantic_title_filter_and_dynamic_exemption():
    """
    Test Semantic Filter on listings:
    1. When searching "Switch" (no accessory keywords):
       - "Nintendo Switch 本体" ($200) -> SURVIVES
       - "Switch ケース" ($15) -> KILLED by active blacklist term 'ケース'
       - "Switch 空箱" ($5) -> KILLED by active blacklist term '空箱'
       - "Switch ジャンク品" ($30) -> KILLED by active blacklist term 'ジャンク'
       - "Switch用 グリップ" ($10) -> KILLED by active blacklist term '用'
       - "Switch box only" ($8) -> KILLED by active blacklist term 'box only'
    2. Minimum price is $200 -> 200 * 32.5 * 1.015 = 6597.5 -> 6598 TWD
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Switch ケース", "price": 15},
            {"title": "Switch 空箱", "price": 5},
            {"title": "Switch ジャンク品", "price": 30},
            {"title": "Switch用 グリップ", "price": 10},
            {"title": "Switch box only", "price": 8},
            {"title": "Nintendo Switch 本体", "price": 200},
            {"title": "Nintendo Switch 有機EL 本体", "price": 260},
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("Switch", client=mock_client)
        assert price in (6597, 6598)


@pytest.mark.anyio
async def test_dynamic_exemption_and_median_filter_on_accessories():
    """
    Test that when searching explicitly for an accessory:
    - The accessory term ("ケース") is exempted from active blacklist
    - Still eliminates non-exempt blacklist items (e.g. "空箱" for the case)
    - Statistical Median Filter (discarding < Median * 0.4) catches extreme low-value outliers (e.g. $1 wrap)
    - Valid minimum case ($15) survives and is converted to TWD
    """
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({
        "listings": [
            {"title": "Switch ケース 空箱", "price": 2},            # KILLED by active blacklist "空箱"
            {"title": "Switch ケース ジャンク", "price": 3},          # KILLED by active blacklist "ジャンク"
            {"title": "Switch キャリングケース 薄型", "price": 1.0},   # Survives title filter, but KILLED by Median * 0.4
            {"title": "Switch ケース 耐衝撃", "price": 15.0},         # Survives!
            {"title": "Switch ケース ハードタイプ", "price": 16.0},     # Survives!
            {"title": "Switch ケース 高級レザー", "price": 20.0},      # Survives!
        ]
    })
    mock_client.get = AsyncMock(return_value=mock_resp)

    # Median of surviving [1.0, 15.0, 16.0, 20.0] is (15.0 + 16.0) / 2 = 15.5
    # Cutoff (0.4x) is 15.5 * 0.4 = 6.2 USD
    # $1.0 is discarded (< 6.2)
    # Remaining valid prices: [15.0, 16.0, 20.0] -> minimum is 15.0 USD
    # 15.0 * 32.5 * 1.015 = 494.8125 -> 495 TWD
    with patch.dict(os.environ, {"RAPIDAPI_KEY": "test_env_key"}):
        price = await fetch_mercari_api_price("Switch ケース", client=mock_client)
        assert price == 495

