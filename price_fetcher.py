"""
price_fetcher.py - Third-party API Price Fetching Architecture

Prepares the architecture for fetching real marketplace prices via third-party APIs
(e.g., RapidAPI, SerpApi) with a mock fallback mechanism for Flex UI visual testing
and strict rate-limit error handling.
"""

import asyncio
import json
import logging
import os
import random
import sys
import urllib.parse
from typing import Any, Dict, List, Optional, Union

import aiohttp

from config import settings
from services.pricing import convert_to_twd

logger = logging.getLogger("line_bot.price_fetcher")

# --- Configuration & RapidAPI / Third-Party Constants ---
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

FASHION_RESALE_API_URL: str = os.getenv(
    "FASHION_RESALE_API_URL",
    "https://fashion-resale-api.p.rapidapi.com/search",
)
RAPIDAPI_HOST_FASHION_RESALE: str = os.getenv(
    "RAPIDAPI_HOST_FASHION_RESALE",
    "fashion-resale-api.p.rapidapi.com",
)
RAPIDAPI_HOST_MERCARI: str = RAPIDAPI_HOST_FASHION_RESALE

# Security First: Read strictly from os.getenv("RAPIDAPI_KEY"), no hardcoded key
RAPIDAPI_KEY: str = (
    os.getenv("RAPIDAPI_KEY")
    or getattr(settings, "rapidapi_key", None)
    or ""
).strip()

SERPAPI_KEY: str = (
    getattr(settings, "serpapi_key", None)
    or os.getenv("SERPAPI_KEY")
    or "YOUR_SERPAPI_KEY_HERE"
)
THIRD_PARTY_API_TOKEN: str = (
    getattr(settings, "third_party_api_token", None)
    or os.getenv("THIRD_PARTY_API_TOKEN")
    or "YOUR_THIRD_PARTY_TOKEN_HERE"
)

# RapidAPI Marketplace Hosts
RAPIDAPI_HOST_RAKUTEN: str = os.getenv("RAPIDAPI_HOST_RAKUTEN", "rakuten-item-search.p.rapidapi.com")
SERPAPI_BASE_URL: str = os.getenv("SERPAPI_BASE_URL", "https://serpapi.com/search.json")


class ThirdPartyAPIError(Exception):
    """Base exception for third-party price fetching errors."""
    pass


class RateLimitExceededError(ThirdPartyAPIError):
    """Raised when third-party API rate limit threshold is reached (HTTP 429)."""
    pass


def is_placeholder_key(key: Optional[str]) -> bool:
    """Check if an API key is a placeholder, empty, or not yet provided."""
    if not key or not str(key).strip():
        return True
    upper = str(key).strip().upper()
    return upper.startswith("YOUR_") or "PLACEHOLDER" in upper or upper == "NONE"


def get_mock_plausible_price(platform: str, keyword: str = "") -> int:
    """
    Generate a plausible mock TWD price (e.g., 1500) for UI button testing.

    Args:
        platform: Target platform ('mercari', 'rakuten', 'shopee', etc.).
        keyword: Item search query.

    Returns:
        int: Plausible price in TWD (defaulting to 1500, or a realistic range).
    """
    plat = platform.lower().strip()
    platform_defaults = {
        "mercari": 1500,
        "rakuten": 1650,
        "shopee": 1450,
        "yahoo_tw": 1550,
        "yahoo_jp": 1380,
        "taobao": 1280,
    }
    return platform_defaults.get(plat, 1500)


async def call_mercari_scraper_api(
    jp_keyword: str,
    timeout_seconds: float = 2.5,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    exchange_rate: float = 0.21,
    overseas_fee_rate: float = 0.015,
) -> Optional[int]:
    """
    Perform async GET request to Fashion Resale API on RapidAPI:
    1. Platform Filter:
       - URL: https://fashion-resale-api.p.rapidapi.com/search
       - Querystring: {"q": jp_keyword, "platform": "mercari"}
       - Headers:
           x-rapidapi-host: fashion-resale-api.p.rapidapi.com
           x-rapidapi-key: os.getenv("RAPIDAPI_KEY")
    2. Data Parsing:
       - Parse returned JSON response.
       - Extract price from the first valid item in the 'listings' array.
    3. Currency Conversion:
       - Convert JPY price to TWD (JPY * 0.21 + 1.5% overseas credit card fee).
    """
    clean_kw = jp_keyword.strip()
    if not clean_kw:
        return None

    api_key = os.getenv("RAPIDAPI_KEY") or getattr(settings, "rapidapi_key", None) or RAPIDAPI_KEY
    if is_placeholder_key(api_key):
        raise ThirdPartyAPIError("No RapidAPI key configured in environment.")

    headers = {
        "Accept": "application/json",
        "x-rapidapi-host": RAPIDAPI_HOST_FASHION_RESALE,
        "x-rapidapi-key": api_key,
    }
    params = {
        "q": clean_kw,
        "platform": "mercari",
    }
    url = FASHION_RESALE_API_URL

    timeout_config = aiohttp.ClientTimeout(
        total=timeout_seconds,
        connect=min(timeout_seconds, 1.2),
    )

    if client is not None:
        # Support mock client with get
        resp = await client.get(url, headers=headers, params=params, timeout=timeout_seconds)
        status_code = getattr(resp, "status_code", getattr(resp, "status", 200))
        text = resp.text if isinstance(resp.text, str) else await resp.text()
    else:
        close_session = False
        if session is None or session.closed:
            session = aiohttp.ClientSession(trust_env=False)
            close_session = True
        try:
            async with session.get(url, headers=headers, params=params, timeout=timeout_config) as resp:
                status_code = resp.status
                text = await resp.text()
        finally:
            if close_session:
                await session.close()

    # Rate Limit Interception
    if status_code == 429:
        raise RateLimitExceededError("Fashion Resale RapidAPI rate limit exceeded (HTTP 429)")

    if status_code != 200:
        raise ThirdPartyAPIError(f"Fashion Resale RapidAPI returned HTTP {status_code}: {text[:100]}")

    try:
        data = json.loads(text)
    except Exception as exc:
        raise ThirdPartyAPIError(f"Failed to parse Fashion Resale response JSON: {exc}")

    # Check for rate limit indicators in payload
    if isinstance(data, dict):
        msg = str(data.get("message", "")).lower()
        if "rate limit" in msg or "quota exceeded" in msg or "too many requests" in msg:
            raise RateLimitExceededError(f"Fashion Resale API quota exceeded: {msg}")

    # Extract listings array
    listings = []
    if isinstance(data, dict):
        listings = data.get("listings") or data.get("items") or data.get("data") or []
    elif isinstance(data, list):
        listings = data

    # Extract price from the first valid item in the 'listings' array
    first_price = None
    for it in listings:
        if isinstance(it, dict):
            raw_p = it.get("price") or it.get("current_price") or it.get("extracted_price")
            if raw_p is not None:
                try:
                    clean_str = (
                        str(raw_p)
                        .replace("¥", "")
                        .replace("円", "")
                        .replace(",", "")
                        .replace("NT$", "")
                        .replace("$", "")
                        .strip()
                    )
                    val = float(clean_str)
                    if val > 0:
                        first_price = val
                        break
                except (ValueError, TypeError):
                    continue
        elif isinstance(it, (int, float)) and it > 0:
            first_price = float(it)
            break

    if first_price is None:
        return None

    # Convert JPY price to TWD (JPY * 0.21 + 1.5% overseas credit card fee)
    twd = convert_to_twd(
        first_price,
        currency="JPY",
        exchange_rate=exchange_rate,
        overseas_fee_rate=overseas_fee_rate,
    )
    return int(round(twd))


async def call_third_party_api(
    platform: str,
    keyword: str,
    timeout_seconds: float = 2.5,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
) -> Optional[int]:
    """
    Dispatch request to third-party API provider (RapidAPI or SerpApi).
    Raises RateLimitExceededError on HTTP 429 and ThirdPartyAPIError on other failures.
    """
    plat = platform.lower().strip()
    clean_kw = keyword.strip()

    # Route Mercari requests to dedicated POST scraper API
    if plat in ("mercari", "mercari_jp", "buyee"):
        return await call_mercari_scraper_api(
            jp_keyword=clean_kw,
            timeout_seconds=timeout_seconds,
            session=session,
            client=client,
        )

    headers = {
        "User-Agent": "LineBot-PriceFetcher/1.0",
        "Accept": "application/json",
    }

    if not is_placeholder_key(RAPIDAPI_KEY):
        # RapidAPI Integration Architecture for Rakuten
        headers.update({
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST_RAKUTEN,
        })
        url = f"https://{RAPIDAPI_HOST_RAKUTEN}/search?query={urllib.parse.quote(clean_kw)}"
    elif not is_placeholder_key(SERPAPI_KEY):
        # SerpApi Integration Architecture
        params = urllib.parse.urlencode({
            "engine": "google_shopping",
            "q": clean_kw,
            "api_key": SERPAPI_KEY,
            "gl": "jp" if plat in ("mercari", "rakuten") else "tw",
            "hl": "ja" if plat in ("mercari", "rakuten") else "zh-TW",
        })
        url = f"{SERPAPI_BASE_URL}?{params}"
    else:
        raise ThirdPartyAPIError("No third-party API credentials configured.")

    timeout_config = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(timeout_seconds, 1.2))

    if client is not None:
        resp = await client.get(url, headers=headers, timeout=timeout_seconds)
        status_code = getattr(resp, "status_code", getattr(resp, "status", 200))
        text = resp.text if isinstance(resp.text, str) else await resp.text()
    else:
        close_session = False
        if session is None or session.closed:
            session = aiohttp.ClientSession(trust_env=False)
            close_session = True
        try:
            async with session.get(url, headers=headers, timeout=timeout_config) as resp:
                status_code = resp.status
                text = await resp.text()
        finally:
            if close_session:
                await session.close()

    # Rate Limit Interception
    if status_code == 429:
        raise RateLimitExceededError(f"Third-party API rate limit exceeded (HTTP 429) for {platform}")

    if status_code != 200:
        raise ThirdPartyAPIError(f"Third-party API returned HTTP {status_code}: {text[:100]}")

    data = json.loads(text)
    # Check for rate limit indicators in payload
    if isinstance(data, dict):
        message = str(data.get("message", "")).lower()
        if "rate limit" in message or "quota exceeded" in message or "too many requests" in message:
            raise RateLimitExceededError(f"API quota exceeded: {message}")

        items = data.get("items") or data.get("shopping_results") or data.get("products") or []
        for it in items[:5]:
            p = it.get("price") or it.get("extracted_price") or it.get("itemPrice")
            if p:
                try:
                    num_val = float(str(p).replace(",", "").replace("¥", "").strip())
                    if num_val > 0:
                        # Convert JPY to TWD with 1.5% overseas fee
                        twd = convert_to_twd(num_val, currency="JPY", overseas_fee_rate=0.015)
                        return int(round(twd))
                except (ValueError, TypeError):
                    continue

    return None


async def fetch_price(
    platform: str,
    keyword: str,
    timeout_seconds: float = 2.5,
    enable_mock: bool = True,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
) -> Optional[int]:
    """
    Asynchronous function to fetch price in TWD for a specified platform and keyword.

    Workflow:
    1. If real third-party API credentials (RAPIDAPI_KEY or SERPAPI_KEY) are present,
       dispatches request to the third-party endpoint (Mercari POST or Rakuten GET).
    2. If the API rate limit is exceeded (HTTP 429) or request fails/times out,
       gracefully catches the exception, logs a warning, and returns None (activating UI fallback).
    3. If no third-party API credentials are configured and enable_mock is True,
       falls back to a mock implementation returning plausible prices (e.g. 1500)
       so the Flex Message button formatting can be verified visually.

    Args:
        platform: Marketplace platform name ('mercari', 'rakuten', 'shopee', etc.).
        keyword: Search query string.
        timeout_seconds: Strict network timeout in seconds (default 2.5s).
        enable_mock: Whether to return plausible mock price when no API key is set (default True).
        session: Optional pre-configured aiohttp.ClientSession.
        client: Optional pre-configured mock client.

    Returns:
        Optional[int]: Calculated or mock price in TWD, or None on failure/rate-limit.
    """
    if not keyword or not keyword.strip():
        return None

    clean_kw = keyword.strip()
    plat = platform.lower().strip()

    # Determine whether third-party API is configured
    has_api_key = not is_placeholder_key(RAPIDAPI_KEY) or not is_placeholder_key(SERPAPI_KEY) or client is not None

    if has_api_key:
        try:
            logger.info(f"🌐 [Third-Party API] Fetching price for {plat}: '{clean_kw}'")
            price = await call_third_party_api(
                platform=plat,
                keyword=clean_kw,
                timeout_seconds=timeout_seconds,
                session=session,
                client=client,
            )
            if price is not None and price > 0:
                return price
        except RateLimitExceededError as rle:
            # Explicit requirement: rate limit exceeded must return None for "(點擊查看)" fallback
            logger.warning(f"🛑 [Rate Limit Intercepted] {rle}. Returning None for UI fallback.")
            return None
        except (ThirdPartyAPIError, asyncio.TimeoutError, aiohttp.ClientError, Exception) as exc:
            # Explicit requirement: failure must gracefully catch exception and return None
            logger.warning(f"⚠️ [Third-Party API Failed] {exc}. Returning None for UI fallback.")
            return None

    # Mock Implementation: Return plausible price (e.g., 1500) when enabled
    if enable_mock:
        mock_price = get_mock_plausible_price(plat, clean_kw)
        logger.debug(f"🧪 [Mock Price Fetcher] Generated mock price for {plat} '{clean_kw}': NT${mock_price}")
        return mock_price

    return None


async def fetch_mercari_api_price(
    jp_keyword: str,
    timeout_seconds: float = 2.5,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    enable_mock: bool = False,
) -> Optional[int]:
    """
    Fetch real Mercari min price in TWD via RapidAPI POST endpoint with strict 2.5s timeout.
    Returns None on failure, timeout, or rate-limit for graceful fallback to '(點擊查看)'.
    """
    return await fetch_price(
        platform="mercari",
        keyword=jp_keyword,
        timeout_seconds=timeout_seconds,
        enable_mock=enable_mock,
        session=session,
        client=client,
    )


def format_platform_button_component(
    platform_name: str,
    price: Optional[Union[int, float, str]],
    target_url: str,
    color: Optional[str] = None,
    suffix: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Format a LINE Flex Message ButtonComponent with dynamic price or fallback text:
    - If price is provided: "text": "{platform_name} (約 NT${price}起)" (default "起" for Mercari)
    - If price is None/0:   "text": "{platform_name} (點擊查看)"
    """
    has_price = price is not None and str(price).strip() not in ("", "0")
    if has_price:
        clean_price_str = str(price).strip()
        eff_suffix = suffix if suffix is not None else ("起" if "mercari" in platform_name.lower() else "")
        if eff_suffix and not clean_price_str.endswith(eff_suffix):
            price_label = f"{clean_price_str}{eff_suffix}"
        else:
            price_label = clean_price_str
        btn_label = f"{platform_name} (約 NT${price_label})"
    else:
        btn_label = f"{platform_name} (點擊查看)"

    button_dict: Dict[str, Any] = {
        "type": "button",
        "style": "primary",
        "height": "sm",
        "text": btn_label,
        "action": {
            "type": "uri",
            "label": btn_label,
            "uri": target_url,
        },
    }
    if color:
        button_dict["color"] = color

    return button_dict


def inject_mercari_button_to_flex(
    flex_dict: Dict[str, Any],
    twd_price: Optional[Union[int, float, str]],
) -> Dict[str, Any]:
    """
    Inject calculated TWD price or fallback into Flex Message payload:
    - If twd_price is valid: "text": "Mercari (約 NT${twd_price}起)"
    - If twd_price is None/0: "text": "Mercari (點擊查看)"
    """
    if not isinstance(flex_dict, dict):
        return flex_dict

    has_price = twd_price is not None and str(twd_price).strip() not in ("", "0")
    if has_price:
        price_str = str(twd_price).strip()
        if not price_str.endswith("起"):
            price_str = f"{price_str}起"
        btn_label = f"Mercari (約 NT${price_str})"
    else:
        btn_label = "Mercari (點擊查看)"

    def _walk_and_update(node: Any):
        if isinstance(node, dict):
            if node.get("type") == "button":
                action = node.get("action")
                uri = action.get("uri", "") if isinstance(action, dict) else ""
                label = action.get("label", "") if isinstance(action, dict) else ""
                btn_text = node.get("text", "")
                if "mercari" in uri.lower() or "mercari" in label.lower() or "mercari" in btn_text.lower():
                    node["text"] = btn_label
                    if isinstance(action, dict):
                        action["label"] = btn_label
            for v in node.values():
                _walk_and_update(v)
        elif isinstance(node, list):
            for item in node:
                _walk_and_update(item)

    _walk_and_update(flex_dict)
    return flex_dict
