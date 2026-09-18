"""
Main application alias for main.py (FastAPI application & LINE webhook).
Provides entrypoint and re-exports app, GEMINI_VISION_PROMPT, and handlers.
"""

from main import (  # noqa: F401
    DynamicPriceResult,
    GEMINI_VISION_PROMPT,
    MessageAction,
    QuickReply,
    QuickReplyItem,
    TextSendMessage,
    app,
    calculate_dynamic_platform_prices,
    construct_platform_search_url,
    convert_to_twd,
    fetch_and_generate_flex_message,
    fetch_lightweight_platform_min_price,
    fetch_lightweight_prices,
    fetch_mercari_min_price,
    fetch_rakuten_min_price,
    filter_extreme_low_prices,
    handler,
    parse_platform_first_page_prices,
    remove_outliers,
)

# Re-export all public symbols from main
from main import *  # noqa: F401, F403

# Re-export third-party API price fetcher architecture
from price_fetcher import (  # noqa: F401
    FASHION_RESALE_API_URL,
    RAPIDAPI_HOST_FASHION_RESALE,
    RAPIDAPI_HOST_MERCARI,
    RAPIDAPI_HOST_RAKUTEN,
    RAPIDAPI_HOST_SHOPEE,
    RAPIDAPI_KEY,
    RAPIDAPI_KEY_SHOPEE,
    SERPAPI_KEY,
    SHOPEE_API_URL,
    SHOPEE_BASE_BLACKLIST,
    THIRD_PARTY_API_TOKEN,
    RateLimitExceededError,
    ThirdPartyAPIError,
    call_mercari_scraper_api,
    call_third_party_api,
    fetch_mercari_api_price,
    fetch_price,
    fetch_shopee_api_price,
    format_platform_button_component,
    get_mock_plausible_price,
    get_shopee_active_blacklist,
    inject_mercari_button_to_flex,
    is_placeholder_key,
)
