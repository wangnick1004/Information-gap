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
import statistics
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

# Shopee RapidAPI Configuration: Read strictly via os.getenv(), no hardcoded key
RAPIDAPI_KEY_SHOPEE: str = (
    os.getenv("RAPIDAPI_KEY_SHOPEE")
    or getattr(settings, "rapidapi_key_shopee", None)
    or ""
).strip()
RAPIDAPI_HOST_SHOPEE: str = (
    os.getenv("RAPIDAPI_HOST_SHOPEE")
    or getattr(settings, "rapidapi_host_shopee", None)
    or "shopee-api.p.rapidapi.com"
).strip()
SHOPEE_API_URL: str = (
    os.getenv("SHOPEE_API_URL")
    or getattr(settings, "shopee_api_url", None)
    or (f"https://{RAPIDAPI_HOST_SHOPEE}/search" if RAPIDAPI_HOST_SHOPEE else "")
)

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

# Base Blacklist Definition for Title Filtering (Mercari)
BASE_BLACKLIST: List[str] = [
    "ケース",
    "カバー",
    "フィルム",
    "空箱",
    "ジャンク",
    "保護",
    "パーツ",
    "部品",
    "用",
    "box only",
]

# Shopee Base Blacklist for Low-Cost Accessories & Noise
SHOPEE_BASE_BLACKLIST: List[str] = [
    # Screen protectors & films
    "保護貼",
    "玻璃貼",
    "鋼化膜",
    "保護膜",
    "貼膜",
    "鏡頭貼",
    # Cases & covers
    "保護殼",
    "手機殼",
    "保護套",
    "防摔殼",
    "清水套",
    "果凍套",
    "矽膠套",
    # Empty boxes, parts, junk
    "空盒",
    "空箱",
    "零件",
    "配件",
    "零件機",
    "故障品",
    "報廢",
    # Cross-language noise terms
    "ケース",
    "カバー",
    "フィルム",
    "空箱",
    "ジャンク",
    "保護",
    "パーツ",
    "部品",
    "box only",
    "case",
    "cover",
    "film",
    "protector",
]

# Semantic accessory groups for dynamic category exemption
SCREEN_PROTECTOR_TERMS: List[str] = [
    "保護貼",
    "玻璃貼",
    "鋼化膜",
    "保護膜",
    "貼膜",
    "鏡頭貼",
    "フィルム",
    "film",
    "protector",
]

CASE_COVER_TERMS: List[str] = [
    "保護殼",
    "手機殼",
    "保護套",
    "防摔殼",
    "清水套",
    "果凍套",
    "矽膠套",
    "ケース",
    "カバー",
    "case",
    "cover",
]


def get_shopee_active_blacklist(keyword: str) -> List[str]:
    """
    Generate dynamic active blacklist for Shopee.
    - If user explicitly searches for screen protectors (e.g. '保護貼'), exempt screen protector terms.
    - If user explicitly searches for cases (e.g. '保護殼', '手機殼'), exempt case/cover terms.
    - Retains empty box / junk / parts filtering unless explicitly requested.
    """
    if not keyword:
        return list(SHOPEE_BASE_BLACKLIST)
    kw_lower = keyword.lower()
    exempt_set = set()

    # Check screen protector group
    if any(t.lower() in kw_lower for t in SCREEN_PROTECTOR_TERMS):
        exempt_set.update(t.lower() for t in SCREEN_PROTECTOR_TERMS)

    # Check case/cover group
    if any(t.lower() in kw_lower for t in CASE_COVER_TERMS):
        exempt_set.update(t.lower() for t in CASE_COVER_TERMS)

    # Directly exempt any individual blacklist term present in keyword
    for t in SHOPEE_BASE_BLACKLIST:
        if t.lower() in kw_lower:
            exempt_set.add(t.lower())

    return [term for term in SHOPEE_BASE_BLACKLIST if term.lower() not in exempt_set]


def get_active_blacklist(
    keyword: str,
    base_blacklist: Optional[List[str]] = None,
) -> List[str]:
    """
    Generate dynamic active blacklist by exempting any terms present in keyword (case-insensitive check).
    ONLY add a term to the active_blacklist if that term is NOT present in the user's requested keyword.
    """
    if base_blacklist == SHOPEE_BASE_BLACKLIST:
        return get_shopee_active_blacklist(keyword)

    blacklist = base_blacklist if base_blacklist is not None else BASE_BLACKLIST
    if not keyword:
        return list(blacklist)
    kw_lower = keyword.lower()
    return [term for term in blacklist if term.lower() not in kw_lower]


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
    timeout_seconds: float = 8.0,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    exchange_rate: float = 32.5,
    overseas_fee_rate: float = 0.015,
    estimated_min_usd: Optional[int] = None,
) -> Optional[int]:
    """
    Perform async GET request to Fashion Resale API on RapidAPI:
    1. Platform Filter:
       - URL: https://fashion-resale-api.p.rapidapi.com/search
       - Querystring: {"q": jp_keyword, "platform": "mercari"}
       - Headers:
           x-rapidapi-host: fashion-resale-api.p.rapidapi.com
           x-rapidapi-key: os.getenv("RAPIDAPI_KEY")
    2. Data Parsing & Dynamic Filtering:
       - Parse returned JSON response.
       - Extract all valid numeric prices from the 'listings' array treated as USD.
       - Filter out any accessory/noise price:
         if item_price_usd >= (estimated_min_usd * 0.6):
         (allows 40% margin for genuine super-bargains while killing cheap accessories).
       - If filtered list is empty, return None.
       - Find the minimum price from this filtered list.
    3. Currency Normalization:
       - Convert this valid minimum USD price to TWD (USD * 32.5 * 1.015) before returning to UI.
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

    logger.info(f"📊 [Mercari Listings] Total items fetched: {len(listings)}")

    # 1. Dynamic Active Blacklist: Exempt terms present in clean_kw (case-insensitive)
    active_blacklist = get_active_blacklist(clean_kw)
    logger.info(
        f"🛡️ [Active Blacklist] Keyword: '{clean_kw}', "
        f"Active: {active_blacklist}, "
        f"Exempted: {[t for t in BASE_BLACKLIST if t.lower() in clean_kw.lower()]}"
    )

    # 2. Semantic Title Filtering & USD Price Extraction
    surviving_prices_usd: List[float] = []
    for it in listings:
        if isinstance(it, dict):
            title = str(it.get("title") or it.get("name") or it.get("item_name") or "").strip()
            title_lower = title.lower()

            # Discard if title contains any term in active_blacklist (case-insensitive check)
            if any(term.lower() in title_lower for term in active_blacklist):
                logger.debug(f"🚫 [Blacklist Discarded] '{title}' matched active blacklist")
                continue

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
                        surviving_prices_usd.append(val)
                except (ValueError, TypeError):
                    continue
        elif isinstance(it, (int, float)) and it > 0:
            surviving_prices_usd.append(float(it))

    logger.info(f"💰 [Mercari Raw Prices (USD)] Surviving prices after title filter: {surviving_prices_usd}")

    # If no listings survived semantic filter, return None
    if not surviving_prices_usd:
        logger.warning("⚠️ [Filter Empty] No Mercari items survived semantic title blacklist.")
        return None

    # 3. Statistical Median Filter: Discard prices < Median * 0.4
    med_price = statistics.median(surviving_prices_usd)
    median_cutoff = med_price * 0.4
    logger.info(f"📊 [Median Filter] Median USD: {med_price:.2f}, Cutoff (0.4x): {median_cutoff:.2f}")

    # 4. Dynamic LLM threshold: item_price_usd >= (estimated_min_usd * 0.6)
    llm_threshold_usd = (
        (estimated_min_usd * 0.6)
        if (estimated_min_usd is not None and estimated_min_usd > 0)
        else 0.0
    )

    # 5. Apply filters: discard prices < Median * 0.4 and < (estimated_min_usd * 0.6)
    filtered_prices = [
        p for p in surviving_prices_usd
        if p >= median_cutoff and p >= llm_threshold_usd
    ]

    # If the filtered list is empty, return None
    if not filtered_prices:
        logger.warning(
            f"⚠️ [Filter Empty] All Mercari items were below cutoffs (Median*0.4={median_cutoff:.2f} USD, LLM*0.6={llm_threshold_usd:.2f} USD) and filtered out."
        )
        return None

    # 6. Minimum valid price from surviving items
    min_price_usd = min(filtered_prices)

    # 7. Currency Normalization: Convert valid minimum USD price to TWD
    twd = convert_to_twd(
        min_price_usd,
        currency="USD",
        exchange_rate=exchange_rate,
        overseas_fee_rate=overseas_fee_rate,
    )
    return int(round(twd))


async def fetch_shopee_api_price(
    keyword: str,
    timeout_seconds: float = 8.0,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    estimated_min_usd: Optional[int] = None,
    enable_mock: bool = False,
) -> Optional[int]:
    """
    Perform async GET request to Shopee search API on RapidAPI:
    1. Environment & Credentials:
       - Strictly reads RAPIDAPI_KEY_SHOPEE and RAPIDAPI_HOST_SHOPEE via os.getenv().
       - Headers:
           x-rapidapi-host: RAPIDAPI_HOST_SHOPEE
           x-rapidapi-key: RAPIDAPI_KEY_SHOPEE
    2. Data Parsing & Smart Filtering:
       - Parse returned JSON response across common schemas (items, data, listings, products, results).
       - Dynamic Exemption: Exempt accessory terms if explicitly searched for, while keeping empty boxes/parts blocked.
       - Discard items matching active title blacklist (filtering out cheap cases, screen protectors, etc.).
       - Statistical Median Filter: Discard prices < Median * 0.4.
       - Optional dynamic LLM threshold: item_price_twd >= (estimated_min_usd * 32.5 * 0.6).
       - Find minimum valid price from surviving items.
    3. Currency & Fallback:
       - Returned as an integer in TWD.
       - If API times out (HTTP 408/504 / asyncio.TimeoutError), hits rate limit (HTTP 429),
         or finds no valid items, gracefully catches error, logs warning, and returns None.
    """
    clean_kw = keyword.strip()
    if not clean_kw:
        return None

    api_key = (
        os.getenv("RAPIDAPI_KEY_SHOPEE")
        if os.getenv("RAPIDAPI_KEY_SHOPEE") is not None
        else RAPIDAPI_KEY_SHOPEE
    )
    api_host = (
        os.getenv("RAPIDAPI_HOST_SHOPEE")
        if os.getenv("RAPIDAPI_HOST_SHOPEE") is not None
        else (RAPIDAPI_HOST_SHOPEE or "shopee-api.p.rapidapi.com")
    ).strip()

    if client is None and is_placeholder_key(api_key):
        if enable_mock:
            mock_p = get_mock_plausible_price("shopee", clean_kw)
            logger.debug(f"🧪 [Mock Price Fetcher] Generated mock price for Shopee '{clean_kw}': NT${mock_p}")
            return mock_p
        logger.warning("⚠️ [Shopee API] No RapidAPI Shopee key configured in environment.")
        return None

    url = (
        os.getenv("SHOPEE_API_URL")
        or getattr(settings, "shopee_api_url", None)
        or f"https://{api_host}/search"
    )
    headers = {
        "Accept": "application/json",
        "x-rapidapi-host": api_host,
        "x-rapidapi-key": api_key,
    }
    params = {
        "keyword": clean_kw,
        "q": clean_kw,
        "site": "tw",
    }

    timeout_config = aiohttp.ClientTimeout(
        total=timeout_seconds,
        connect=min(timeout_seconds, 1.2),
    )

    status_code = 200
    text = ""

    logger.info(f"🌐 [Third-Party API] Fetching price for shopee: '{clean_kw}'")
    try:
        if client is not None:
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
    except asyncio.TimeoutError:
        logger.warning(f"⚠️ [Timeout] Shopee RapidAPI request exceeded limit ({timeout_seconds}s).")
        return None
    except (aiohttp.ClientError, Exception) as exc:
        logger.warning(f"⚠️ [Shopee API Request Failed] {exc}. Returning None for UI fallback.")
        return None

    # Rate Limit Interception (HTTP 429)
    if status_code == 429:
        logger.warning("🛑 [Rate Limit Intercepted] Shopee RapidAPI rate limit exceeded (HTTP 429). Returning None for UI fallback.")
        return None

    # Timeout Interception (HTTP 408 / 504)
    if status_code in (408, 504):
        logger.warning(f"⚠️ [Timeout Intercepted] Shopee RapidAPI timeout (HTTP {status_code}). Returning None for UI fallback.")
        return None

    # Other HTTP Failures
    if status_code != 200:
        logger.warning(f"⚠️ [Shopee API Failed] Shopee RapidAPI returned HTTP {status_code}: {text[:100]}. Returning None for UI fallback.")
        return None

    try:
        data = json.loads(text)
    except Exception as exc:
        logger.warning(f"⚠️ [Shopee API Failed] Failed to parse Shopee response JSON: {exc}")
        return None

    # Check for rate limit indicators in payload
    if isinstance(data, dict):
        msg = str(data.get("message", "")).lower()
        if "rate limit" in msg or "quota exceeded" in msg or "too many requests" in msg:
            logger.warning(f"🛑 [Rate Limit Intercepted] Shopee API quota exceeded: {msg}")
            return None

    # Extract listings array
    listings = []
    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            listings = data["items"]
        elif "data" in data:
            if isinstance(data["data"], list):
                listings = data["data"]
            elif isinstance(data["data"], dict):
                listings = (
                    data["data"].get("items")
                    or data["data"].get("products")
                    or data["data"].get("listings")
                    or []
                )
        elif "listings" in data and isinstance(data["listings"], list):
            listings = data["listings"]
        elif "products" in data and isinstance(data["products"], list):
            listings = data["products"]
        elif "results" in data and isinstance(data["results"], list):
            listings = data["results"]
    elif isinstance(data, list):
        listings = data

    logger.info(f"📊 [Shopee Listings] Total items fetched: {len(listings)}")
    if not listings:
        logger.warning("⚠️ [Filter Empty] No listings found in Shopee API response.")
        return None

    # 1. Dynamic Active Blacklist: Exempt terms present in clean_kw (case-insensitive)
    active_blacklist = get_shopee_active_blacklist(clean_kw)
    logger.info(
        f"🛡️ [Shopee Active Blacklist] Keyword: '{clean_kw}', "
        f"Active terms count: {len(active_blacklist)}"
    )

    # 2. Semantic Title Filtering & TWD Price Extraction
    surviving_prices_twd: List[float] = []
    for it in listings:
        if isinstance(it, dict):
            title = str(
                it.get("title")
                or it.get("name")
                or it.get("item_name")
                or (it.get("item_basic", {}).get("name") if isinstance(it.get("item_basic"), dict) else "")
                or ""
            ).strip()
            title_lower = title.lower()

            # Discard if title contains any term in active_blacklist (case-insensitive check)
            if any(term.lower() in title_lower for term in active_blacklist):
                logger.debug(f"🚫 [Shopee Blacklist Discarded] '{title}' matched active blacklist")
                continue

            raw_p = (
                it.get("price")
                or it.get("current_price")
                or it.get("extracted_price")
                or it.get("price_min")
                or (it.get("item_basic", {}).get("price") if isinstance(it.get("item_basic"), dict) else None)
            )
            if raw_p is not None:
                try:
                    clean_str = (
                        str(raw_p)
                        .replace("NT$", "")
                        .replace("NT", "")
                        .replace("$", "")
                        .replace("¥", "")
                        .replace("円", "")
                        .replace(",", "")
                        .strip()
                    )
                    val = float(clean_str)
                    # Handle raw Shopee micro-units if val > 1_000_000 (e.g. 150000000 -> 1500)
                    if val > 1_000_000:
                        val = val / 100_000.0
                    if val > 0:
                        surviving_prices_twd.append(val)
                except (ValueError, TypeError):
                    continue
        elif isinstance(it, (int, float)) and it > 0:
            surviving_prices_twd.append(float(it))

    logger.info(f"💰 [Shopee Raw Prices (TWD)] Surviving prices after title filter: {surviving_prices_twd}")

    # If no listings survived semantic filter, return None
    if not surviving_prices_twd:
        logger.warning("⚠️ [Filter Empty] No Shopee items survived semantic title blacklist.")
        return None

    # 3. Statistical Median Filter: Discard prices < Median * 0.4
    med_price = statistics.median(surviving_prices_twd)
    median_cutoff = med_price * 0.4
    logger.info(f"📊 [Shopee Median Filter] Median TWD: {med_price:.2f}, Cutoff (0.4x): {median_cutoff:.2f}")

    # 4. Dynamic LLM threshold: item_price_twd >= (estimated_min_usd * 32.5 * 0.6)
    llm_threshold_twd = (
        (estimated_min_usd * 32.5 * 0.6)
        if (estimated_min_usd is not None and estimated_min_usd > 0)
        else 0.0
    )

    # 5. Apply filters: discard prices < Median * 0.4 and < llm_threshold_twd
    filtered_prices = [
        p for p in surviving_prices_twd
        if p >= median_cutoff and p >= llm_threshold_twd
    ]

    # If the filtered list is empty, return None
    if not filtered_prices:
        logger.warning(
            f"⚠️ [Filter Empty] All Shopee items were below cutoffs (Median*0.4={median_cutoff:.2f} TWD, LLM threshold={llm_threshold_twd:.2f} TWD) and filtered out."
        )
        return None

    # 6. Minimum valid price from surviving items (returned as an integer in TWD)
    min_price_twd = min(filtered_prices)
    return int(round(min_price_twd))


async def call_third_party_api(
    platform: str,
    keyword: str,
    timeout_seconds: float = 8.0,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    estimated_min_usd: Optional[int] = None,
) -> Optional[int]:
    """
    Dispatch request to third-party API provider (RapidAPI or SerpApi).
    Raises RateLimitExceededError on HTTP 429 and ThirdPartyAPIError on other failures.
    """
    plat = platform.lower().strip()
    clean_kw = keyword.strip()

    # Route Shopee requests to dedicated fetch_shopee_api_price
    if plat in ("shopee", "shopee_tw"):
        return await fetch_shopee_api_price(
            keyword=clean_kw,
            timeout_seconds=timeout_seconds,
            session=session,
            client=client,
            estimated_min_usd=estimated_min_usd,
        )

    # Route Mercari requests to dedicated POST scraper API
    if plat in ("mercari", "mercari_jp", "buyee"):
        return await call_mercari_scraper_api(
            jp_keyword=clean_kw,
            timeout_seconds=timeout_seconds,
            session=session,
            client=client,
            estimated_min_usd=estimated_min_usd,
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
    timeout_seconds: float = 8.0,
    enable_mock: bool = True,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    estimated_min_usd: Optional[int] = None,
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
        timeout_seconds: Strict network timeout in seconds (default 8.0s).
        enable_mock: Whether to return plausible mock price when no API key is set (default True).
        session: Optional pre-configured aiohttp.ClientSession.
        client: Optional pre-configured mock client.
        estimated_min_usd: Optional LLM-estimated minimum USD price threshold for filtering.

    Returns:
        Optional[int]: Calculated or mock price in TWD, or None on failure/rate-limit.
    """
    if not keyword or not keyword.strip():
        return None

    clean_kw = keyword.strip()
    plat = platform.lower().strip()
    # Route Shopee platform requests directly to fetch_shopee_api_price
    if plat in ("shopee", "shopee_tw"):
        shopee_api_key = (
            os.getenv("RAPIDAPI_KEY_SHOPEE")
            if os.getenv("RAPIDAPI_KEY_SHOPEE") is not None
            else RAPIDAPI_KEY_SHOPEE
        )
        has_shopee_key = not is_placeholder_key(shopee_api_key) or client is not None
        if has_shopee_key:
            try:
                logger.info(f"🌐 [Third-Party API] Fetching price for {plat}: '{clean_kw}'")
                price = await fetch_shopee_api_price(
                    keyword=clean_kw,
                    timeout_seconds=timeout_seconds,
                    session=session,
                    client=client,
                    estimated_min_usd=estimated_min_usd,
                    enable_mock=False,
                )
                if price is not None and price > 0:
                    return price
                return None
            except Exception as exc:
                logger.warning(f"⚠️ [Shopee Fetcher Failed] {exc}. Returning None for UI fallback.")
                return None
        elif enable_mock:
            mock_price = get_mock_plausible_price(plat, clean_kw)
            logger.debug(f"🧪 [Mock Price Fetcher] Generated mock price for {plat} '{clean_kw}': NT${mock_price}")
            return mock_price
        return None

    # Determine whether third-party API is configured for other platforms
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
                estimated_min_usd=estimated_min_usd,
            )
            if price is not None and price > 0:
                return price
        except RateLimitExceededError as rle:
            # Explicit requirement: rate limit exceeded must return None for "(點擊查看)" fallback
            logger.warning(f"🛑 [Rate Limit Intercepted] {rle}. Returning None for UI fallback.")
            return None
        except asyncio.TimeoutError:
            logger.warning("⚠️ [Timeout] Third-Party API request exceeded limit.")
            return None
        except (ThirdPartyAPIError, aiohttp.ClientError, Exception) as exc:
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
    timeout_seconds: float = 8.0,
    session: Optional[aiohttp.ClientSession] = None,
    client: Optional[Any] = None,
    enable_mock: bool = False,
    estimated_min_usd: Optional[int] = None,
) -> Optional[int]:
    """
    Fetch real Mercari min price in TWD via RapidAPI POST endpoint with strict 8.0s timeout.
    Returns None on failure, timeout, or rate-limit for graceful fallback to '(點擊查看)'.
    """
    return await fetch_price(
        platform="mercari",
        keyword=jp_keyword,
        timeout_seconds=timeout_seconds,
        enable_mock=enable_mock,
        session=session,
        client=client,
        estimated_min_usd=estimated_min_usd,
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


def inject_shopee_button_to_flex(
    flex_dict: Dict[str, Any],
    twd_price: Optional[Union[int, float, str]],
) -> Dict[str, Any]:
    """
    Inject calculated TWD price or fallback into Flex Message payload for Shopee:
    - If twd_price is valid: "text": "台灣蝦皮 (約 NT${price_str})" (or "Shopee (約 NT${price_str})")
    - If twd_price is None/0: "text": "台灣蝦皮 (點擊查看)" (or "Shopee (點擊查看)")
    """
    if not isinstance(flex_dict, dict):
        return flex_dict

    has_price = twd_price is not None and str(twd_price).strip() not in ("", "0")
    price_str = ""
    if has_price:
        try:
            num_val = float(str(twd_price).replace(",", ""))
            if num_val <= 0:
                has_price = False
            else:
                price_str = f"{int(round(num_val))}"
        except ValueError:
            price_str = str(twd_price).strip()

    if has_price:
        btn_label_tw = f"台灣蝦皮 (約 NT${price_str})"
        btn_label_en = f"Shopee (約 NT${price_str})"
    else:
        btn_label_tw = "台灣蝦皮 (點擊查看)"
        btn_label_en = "Shopee (點擊查看)"

    def _walk_and_update(node: Any):
        if isinstance(node, dict):
            if node.get("type") == "button":
                action = node.get("action")
                uri = action.get("uri", "") if isinstance(action, dict) else ""
                label = action.get("label", "") if isinstance(action, dict) else ""
                btn_text = node.get("text", "")
                if (
                    "shopee" in uri.lower()
                    or "shopee" in label.lower()
                    or "蝦皮" in label
                    or "shopee" in btn_text.lower()
                    or "蝦皮" in btn_text
                ):
                    is_tw = "蝦皮" in label or "蝦皮" in btn_text
                    target_label = btn_label_tw if is_tw else btn_label_en
                    node["text"] = target_label
                    if isinstance(action, dict):
                        action["label"] = target_label
            for v in node.values():
                _walk_and_update(v)
        elif isinstance(node, list):
            for item in node:
                _walk_and_update(item)

    _walk_and_update(flex_dict)
    return flex_dict

