import logging
import re
import statistics
import time
import urllib.parse
from typing import List, Optional

from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("line_bot.scraper")


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


async def scrape_buyee_prices(
    search_query_ja: str,
    timeout_seconds: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> ScrapingResult:
    """
    Scrape Buyee Mercari listings asynchronously for a given Japanese search query.

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

    encoded_keyword = urllib.parse.quote(clean_query)
    search_url = f"{BUYEE_MERCARI_BASE_URL}?keyword={encoded_keyword}"

    # Print debug URL for manual inspection
    print(f"\n🔍 [Scraper] Query: '{clean_query}'")
    print(f"🌐 [Scraper Target URL]: {search_url}")
    print(f"⏱️ [Scraper] Configured timeout: {timeout_seconds}s")

    headers = dict(DEFAULT_HEADERS)
    timeout_config = httpx.Timeout(timeout_seconds, connect=5.0)

    start_time = time.time()

    try:
        if client:
            response = await client.get(search_url, headers=headers, timeout=timeout_config)
        else:
            async with httpx.AsyncClient(timeout=timeout_config, follow_redirects=True) as async_http_client:
                response = await async_http_client.get(search_url, headers=headers)

        elapsed = time.time() - start_time
        print(f"📥 [Scraper] Response received in {elapsed:.2f}s (HTTP {response.status_code})")

        if response.status_code in (202, 403):
            logger.warning(f"Buyee returned anti-bot challenge status {response.status_code} for query: {clean_query}")
            raise ScrapingBlockedError(
                f"日本代購平台返回驗證狀態 ({response.status_code})",
                search_url=search_url,
                query=clean_query,
            )

        if response.status_code != 200:
            logger.error(f"Buyee returned HTTP status {response.status_code} for query: {clean_query}")
            raise ScrapingError(
                f"Buyee scraping failed with status code {response.status_code}",
                search_url=search_url,
                query=clean_query,
            )

        listings = parse_buyee_html(response.text, max_items=5)

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

        return ScrapingResult(
            query=clean_query,
            search_url=search_url,
            lowest_price_jpy=lowest_price,
            median_price_jpy=median_price,
            representative_image_url=rep_image_url,
            sample_prices=prices,
            total_found=len(listings),
        )

    except (ScrapingTimeoutError, ScrapingBlockedError, ScrapingError):
        raise
    except httpx.TimeoutException as exc:
        elapsed = time.time() - start_time
        print(f"❌ [Scraper Timeout] Request exceeded {timeout_seconds}s (took {elapsed:.2f}s): {exc}")
        logger.error(f"Scraping timeout for query '{clean_query}': {exc}")
        raise ScrapingTimeoutError(
            f"連線日本代購平台逾時 (超過 {timeout_seconds} 秒)",
            search_url=search_url,
            query=clean_query,
        ) from exc
    except httpx.RequestError as exc:
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
