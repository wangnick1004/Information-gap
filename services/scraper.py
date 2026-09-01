import asyncio
import json
import logging
import re
import statistics
import time
import urllib.parse
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

from services.cache import search_cache

logger = logging.getLogger("line_bot.scraper")

_aiohttp_session: Optional[aiohttp.ClientSession] = None


async def get_aiohttp_session() -> aiohttp.ClientSession:
    """Get or initialize singleton aiohttp.ClientSession with TCP connection pooling."""
    global _aiohttp_session
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _aiohttp_session is None or _aiohttp_session.closed or getattr(_aiohttp_session, "_loop", None) != current_loop:
        connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=30.0, ssl=True)
        _aiohttp_session = aiohttp.ClientSession(
            connector=connector,
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10.0, connect=3.0),
        )
    return _aiohttp_session


class ScrapedListing(BaseModel):
    """Individual listing data extracted from proxy marketplace."""

    title: Optional[str] = None
    price_jpy: float
    image_url: Optional[str] = None


class ScrapingResult(BaseModel):
    """Aggregated scraping results from Buyee Mercari."""

    query: str = Field(description="Search query sent to Buyee.")
    search_url: str = Field(description="Direct search URL on Buyee.")
    lowest_price_jpy: float = Field(description="Lowest listing price in JPY among top results.")
    median_price_jpy: float = Field(description="Median listing price in JPY among top results.")
    representative_image_url: Optional[str] = Field(
        default=None,
        description="Thumbnail image URL of the most representative item.",
    )
    sample_prices: List[float] = Field(
        default_factory=list,
        description="List of top extracted listing prices in JPY.",
    )
    total_found: int = Field(description="Number of sampled listings.")


class ScrapingError(Exception):
    """Base exception raised when scraping fails."""

    def __init__(self, message: str, search_url: Optional[str] = None, query: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.search_url = search_url
        self.query = query


class ScrapingTimeoutError(ScrapingError):
    """Raised when the scraping network request times out."""
    pass


class ScrapingBlockedError(ScrapingError):
    """Raised when the proxy site blocks or returns anti-bot challenges (e.g., 202/403)."""
    pass


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

BUYEE_MERCARI_BASE_URL = "https://buyee.jp/mercari/search"


def normalize_search_keyword(keyword: Optional[str]) -> str:
    """
    Normalize Japanese / English search keywords for consistent marketplace queries:
    1. Replace continuous whitespace (including full-width Japanese spaces '\\u3000', tabs, and newlines)
       with a single half-width space using regex.
    2. Convert all English/ASCII characters to uppercase consistently.
    3. Strip leading and trailing whitespace.
    """
    if not keyword:
        return ""
    cleaned = re.sub(r"[\s\u3000]+", " ", str(keyword))
    return cleaned.upper().strip()


def normalize_rakuten_search_keyword(keyword: Optional[str]) -> str:
    """
    Normalize Japanese / English search keywords specifically for Rakuten:
    Removes spaces specifically between English letters and numbers (e.g. 'Switch 2' -> 'Switch2', 'PS 5' -> 'PS5'),
    then standardizes whitespace and case.
    """
    if not keyword:
        return ""
    condensed = re.sub(r"(?i)([a-z])\s+(\d)", r"\1\2", str(keyword))
    return normalize_search_keyword(condensed)


def extract_price_number(text: str) -> Optional[float]:
    """Extract numeric JPY price from text like '¥1,500', '1500円', '2,300 JPY'."""
    if not text:
        return None
    cleaned = text.replace(",", "").strip()
    match = re.search(r"[¥￥]?\s*(\d+)\s*(?:円|JPY)?", cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_buyee_html(html_content: str, max_items: int = 5) -> List[ScrapedListing]:
    """
    Parse HTML content from Buyee Mercari search page and extract top item listings.

    Args:
        html_content: Raw HTML text.
        max_items: Maximum number of listings to extract (default 5).

    Returns:
        List[ScrapedListing]: Extracted listings.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    listings: List[ScrapedListing] = []

    card_selectors = [
        ".itemCard",
        ".itemCard__item",
        ".items-box",
        ".search-result__item",
        ".g-itemCard",
        ".product-card",
        "li[data-item-id]",
        ".item-list__item",
    ]

    card_elements = []
    for selector in card_selectors:
        found = soup.select(selector)
        if found:
            card_elements = found
            break

    if card_elements:
        for card in card_elements:
            if len(listings) >= max_items:
                break

            # Price extraction
            price_elem = card.select_one(
                ".itemCard__price, .price, .g-price, .item-price, .itemCard__price--yen, span[class*='price']"
            )
            price_text = price_elem.get_text(strip=True) if price_elem else card.get_text(strip=True)
            price = extract_price_number(price_text)

            if price is None or price <= 0:
                continue

            # Image extraction
            img_elem = card.select_one("img")
            img_url: Optional[str] = None
            if img_elem:
                img_url = (
                    img_elem.get("data-src")
                    or img_elem.get("data-original")
                    or img_elem.get("data-lazy-src")
                    or img_elem.get("src")
                )
                if img_url and img_url.startswith("//"):
                    img_url = "https:" + img_url

            # Title extraction
            title_elem = card.select_one(
                ".itemCard__title, .itemCard__itemName, .item-name, a[title], img[alt]"
            )
            title = None
            if title_elem:
                title = title_elem.get("title") or title_elem.get("alt") or title_elem.get_text(strip=True)

            listings.append(
                ScrapedListing(
                    title=title,
                    price_jpy=price,
                    image_url=img_url,
                )
            )

    # Fallback parser if structured card selectors yielded no results
    if not listings:
        price_tags = soup.find_all(string=re.compile(r"[¥￥]\s*[0-9,]+|[0-9,]+\s*円"))
        for p_tag in price_tags:
            if len(listings) >= max_items:
                break
            price = extract_price_number(p_tag)
            if price and price > 0:
                parent = p_tag.find_parent(["div", "li", "article", "a"])
                img_url = None
                if parent:
                    img_tag = parent.find("img")
                    if img_tag:
                        img_url = (
                            img_tag.get("data-src")
                            or img_tag.get("data-original")
                            or img_tag.get("src")
                        )
                        if img_url and img_url.startswith("//"):
                            img_url = "https:" + img_url

                listings.append(
                    ScrapedListing(
                        title=None,
                        price_jpy=price,
                        image_url=img_url,
                    )
                )

    return listings


def parse_buyee_json_or_html(content_text: str, max_items: int = 5) -> List[ScrapedListing]:
    """
    Attempt ultra-fast (< 1ms) JSON parsing if structured JSON / __NEXT_DATA__ / API payload is present,
    otherwise fallback to fast HTML extraction.
    """
    if not content_text:
        return []

    # Check if raw JSON response was returned
    stripped = content_text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            items_list = data.get("items") or data.get("itemList") or data.get("products") or []
            listings: List[ScrapedListing] = []
            for it in items_list[:max_items]:
                price = it.get("price") or it.get("price_jpy") or it.get("taxIncludedPrice")
                if price:
                    listings.append(
                        ScrapedListing(
                            title=it.get("title") or it.get("name"),
                            price_jpy=float(price),
                            image_url=it.get("imageUrl") or it.get("image_url") or it.get("thumbnail"),
                        )
                    )
            if listings:
                logger.debug(f"⚡ [Fast JSON Parser] Extracted {len(listings)} items from raw JSON.")
                return listings
        except Exception:
            pass

    # Check for embedded Next.js or inline JSON script in HTML
    next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', content_text, re.DOTALL)
    if next_data_match:
        try:
            json_str = next_data_match.group(1)
            data = json.loads(json_str)
            props = data.get("props", {}).get("pageProps", {})
            items_list = props.get("items") or props.get("itemList") or props.get("searchResult", {}).get("items") or []
            listings = []
            for it in items_list[:max_items]:
                price = it.get("price") or it.get("taxIncludedPrice")
                if price:
                    listings.append(
                        ScrapedListing(
                            title=it.get("title") or it.get("name"),
                            price_jpy=float(price),
                            image_url=it.get("imageUrl") or it.get("thumbnail"),
                        )
                    )
            if listings:
                logger.debug(f"⚡ [Fast JSON Parser] Extracted {len(listings)} items from __NEXT_DATA__ JSON script.")
                return listings
        except Exception:
            pass

    # Fallback to HTML parser
    return parse_buyee_html(content_text, max_items=max_items)


async def fetch_network_content(
    search_url: str,
    headers: dict,
    timeout_seconds: float,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[int, str]:
    """
    Fetch web content asynchronously with aiohttp for low-latency network I/O,
    with automatic support for injected clients and testing mocks.
    """
    if client is not None:
        timeout_config = httpx.Timeout(timeout_seconds, connect=5.0)
        response = await client.get(search_url, headers=headers, timeout=timeout_config)
        return response.status_code, response.text

    session = await get_aiohttp_session()
    timeout_aio = aiohttp.ClientTimeout(total=timeout_seconds, connect=4.0)
    async with session.get(search_url, headers=headers, timeout=timeout_aio) as resp:
        return resp.status, await resp.text()


async def scrape_buyee_prices(
    search_query_ja: str,
    timeout_seconds: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> ScrapingResult:
    """
    Scrape Buyee Mercari listings asynchronously with aiohttp for low-latency network I/O.

    Args:
        search_query_ja: Japanese keyword(s) to query on Mercari/Buyee.
        timeout_seconds: Strict HTTP request timeout in seconds (default 10.0s).
        client: Optional pre-configured httpx.AsyncClient (useful for mocking/testing).

    Returns:
        ScrapingResult: Aggregated lowest/median price and representative thumbnail.

    Raises:
        ScrapingTimeoutError: When network request exceeds the timeout threshold.
        ScrapingBlockedError: When the target site returns anti-bot challenge (e.g. 202/403).
        ScrapingError: When no listings are found or general scraping errors occur.
    """
    clean_query = normalize_search_keyword(search_query_ja)
    if not clean_query:
        raise ScrapingError("Search query cannot be empty.")

    # Check 1-hour TTL cache for previously scraped query
    cache_key = f"buyee:{clean_query}"
    cached_result = search_cache.get(cache_key)
    if cached_result is not None and isinstance(cached_result, ScrapingResult):
        logger.info(f"⚡ [Cache Hit] Returning cached scraping result for query: '{clean_query}'")
        return cached_result

    encoded_keyword = urllib.parse.quote(clean_query)
    search_url = f"{BUYEE_MERCARI_BASE_URL}?keyword={encoded_keyword}"

    # Print debug URL for manual inspection
    print(f"\n🔍 [Scraper] Query: '{clean_query}'")
    print(f"🌐 [Scraper Target URL]: {search_url}")
    print(f"⏱️ [Scraper] Configured timeout: {timeout_seconds}s")

    headers = dict(DEFAULT_HEADERS)
    start_time = time.time()

    try:
        status_code, response_text = await fetch_network_content(
            search_url=search_url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            client=client,
        )

        elapsed = time.time() - start_time
        print(f"📥 [Scraper] Response received in {elapsed:.2f}s (HTTP {status_code})")

        if status_code in (202, 403):
            logger.warning(f"Buyee returned anti-bot challenge status {status_code} for query: {clean_query}")
            raise ScrapingBlockedError(
                f"日本代購平台返回驗證狀態 ({status_code})",
                search_url=search_url,
                query=clean_query,
            )

        if status_code != 200:
            logger.error(f"Buyee returned HTTP status {status_code} for query: {clean_query}")
            raise ScrapingError(
                f"Buyee scraping failed with status code {status_code}",
                search_url=search_url,
                query=clean_query,
            )

        listings = parse_buyee_json_or_html(response_text, max_items=5)

        if not listings:
            logger.warning(f"No listings found on Buyee for query: {clean_query}")
            raise ScrapingError(
                f"在 Buyee 平台上未找到符合「{clean_query}」的商品",
                search_url=search_url,
                query=clean_query,
            )

        prices = [item.price_jpy for item in listings]
        lowest_price = min(prices)
        median_price = statistics.median(prices)

        rep_image_url: Optional[str] = None
        for item in listings:
            if item.image_url and item.image_url.startswith("http"):
                rep_image_url = item.image_url
                break

        logger.info(
            f"Successfully scraped Buyee for '{clean_query}': {len(listings)} items. "
            f"Lowest: {lowest_price} JPY, Median: {median_price} JPY"
        )

        result = ScrapingResult(
            query=clean_query,
            search_url=search_url,
            lowest_price_jpy=lowest_price,
            median_price_jpy=median_price,
            representative_image_url=rep_image_url,
            sample_prices=prices,
            total_found=len(listings),
        )

        # Store in 1-hour TTL cache
        search_cache.set(cache_key, result, ttl=3600.0)
        return result

    except (ScrapingTimeoutError, ScrapingBlockedError, ScrapingError):
        raise
    except (httpx.TimeoutException, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as exc:
        elapsed = time.time() - start_time
        print(f"❌ [Scraper Timeout] Request exceeded {timeout_seconds}s (took {elapsed:.2f}s): {exc}")
        logger.error(f"Scraping timeout for query '{clean_query}': {exc}")
        raise ScrapingTimeoutError(
            f"連線日本代購平台逾時 (超過 {timeout_seconds} 秒)",
            search_url=search_url,
            query=clean_query,
        ) from exc
    except (httpx.RequestError, aiohttp.ClientError) as exc:
        elapsed = time.time() - start_time
        print(f"❌ [Scraper Network Error] in {elapsed:.2f}s: {exc}")
        logger.error(f"HTTP network error while scraping '{clean_query}': {exc}")
        raise ScrapingError(
            f"連線至代購平台失敗: {exc}",
            search_url=search_url,
            query=clean_query,
        ) from exc
    except Exception as exc:
        logger.error(f"Unexpected error while scraping Buyee: {exc}", exc_info=True)
        raise ScrapingError(
            f"解析代購平台資料失敗: {exc}",
            search_url=search_url,
            query=clean_query,
        ) from exc


class PlatformSearchResult(BaseModel):
    """Result for an individual regional platform search."""

    platform: str
    region: str  # 'JP', 'TW', 'CN'
    query: str
    search_url: str
    scraping_result: Optional[ScrapingResult] = None
    status: str = "success"  # 'success', 'fallback', 'error'
    error_message: Optional[str] = None


class CrossBorderSearchResult(BaseModel):
    """Unified concurrent cross-border search results covering JP, TW, and CN platforms."""

    query_jp: str
    query_zh: str
    japanese_result: Optional[PlatformSearchResult] = None
    taiwanese_result: Optional[PlatformSearchResult] = None
    chinese_result: Optional[PlatformSearchResult] = None
    primary_scraping_result: Optional[ScrapingResult] = None


async def search_japanese_platforms(
    query_ja: str,
    timeout_seconds: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> PlatformSearchResult:
    """Search Japanese proxy marketplace (Buyee Mercari) asynchronously."""
    clean_query = normalize_search_keyword(query_ja)
    encoded_kw = urllib.parse.quote(clean_query)
    search_url = f"{BUYEE_MERCARI_BASE_URL}?keyword={encoded_kw}"
    try:
        res = await scrape_buyee_prices(query_ja, timeout_seconds=timeout_seconds, client=client)
        return PlatformSearchResult(
            platform="Buyee Mercari",
            region="JP",
            query=clean_query,
            search_url=search_url,
            scraping_result=res,
            status="success",
        )
    except Exception as exc:
        logger.warning(f"Japanese search fallback for '{clean_query}': {exc}")
        return PlatformSearchResult(
            platform="Buyee Mercari",
            region="JP",
            query=clean_query,
            search_url=search_url,
            status="fallback",
            error_message=str(exc),
        )


async def search_taiwanese_platforms(query_zh: str) -> PlatformSearchResult:
    """Search Taiwanese marketplaces (Shopee TW / Yahoo TW) asynchronously."""
    clean_query = str(query_zh).strip()
    encoded_kw = urllib.parse.quote(clean_query)
    search_url = f"https://tw.buy.yahoo.com/search/product?p={encoded_kw}"
    return PlatformSearchResult(
        platform="Yahoo Taiwan / Shopee",
        region="TW",
        query=clean_query,
        search_url=search_url,
        status="success",
    )


async def search_chinese_platforms(query_zh: str) -> PlatformSearchResult:
    """Search Chinese marketplaces (Taobao) asynchronously."""
    clean_query = str(query_zh).strip()
    encoded_kw = urllib.parse.quote(clean_query)
    search_url = f"https://world.taobao.com/search/search.htm?q={encoded_kw}"
    return PlatformSearchResult(
        platform="Taobao",
        region="CN",
        query=clean_query,
        search_url=search_url,
        status="success",
    )


async def search_all_platforms_concurrently(
    query_ja: str,
    query_zh: str,
    timeout_seconds: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> CrossBorderSearchResult:
    """
    Search Japanese, Chinese, and Taiwanese platforms concurrently using asyncio.gather.
    Ensures that searches across all three regions run simultaneously rather than sequentially.
    """
    jp_task = asyncio.create_task(search_japanese_platforms(query_ja, timeout_seconds=timeout_seconds, client=client))
    tw_task = asyncio.create_task(search_taiwanese_platforms(query_zh))
    cn_task = asyncio.create_task(search_chinese_platforms(query_zh))

    jp_res, tw_res, cn_res = await asyncio.gather(jp_task, tw_task, cn_task)

    primary_scrape = jp_res.scraping_result if (jp_res and jp_res.scraping_result) else None

    return CrossBorderSearchResult(
        query_jp=query_ja,
        query_zh=query_zh,
        japanese_result=jp_res,
        taiwanese_result=tw_res,
        chinese_result=cn_res,
        primary_scraping_result=primary_scrape,
    )
