import logging
import re
import statistics
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
    """Custom exception raised when scraping fails or no results are found."""
    pass


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BUYEE_MERCARI_BASE_URL = "https://buyee.jp/mercari/search"


def extract_price_number(text: str) -> Optional[float]:
    """Extract numeric JPY price from text like '¥1,500', '1500円', '2,300 JPY'."""
    if not text:
        return None
    # Remove common whitespace and commas
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

    # Common card containers used by Buyee Mercari
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

    # If cards found with standard selectors
    if card_elements:
        for card in card_elements:
            if len(listings) >= max_items:
                break

            # 1. Price extraction
            price_elem = card.select_one(
                ".itemCard__price, .price, .g-price, .item-price, .itemCard__price--yen, span[class*='price']"
            )
            price_text = price_elem.get_text(strip=True) if price_elem else card.get_text(strip=True)
            price = extract_price_number(price_text)

            if price is None or price <= 0:
                continue

            # 2. Image extraction
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

            # 3. Title extraction
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

    # Fallback parser if structured selectors failed
    if not listings:
        # Search for any elements containing JPY price patterns
        price_tags = soup.find_all(string=re.compile(r"[¥￥]\s*[0-9,]+|[0-9,]+\s*円"))
        for p_tag in price_tags:
            if len(listings) >= max_items:
                break
            price = extract_price_number(p_tag)
            if price and price > 0:
                # Attempt to find nearest parent container with image
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
    timeout_seconds: float = 4.0,
    client: Optional[httpx.AsyncClient] = None,
) -> ScrapingResult:
    """
    Scrape Buyee Mercari listings asynchronously for a given Japanese search query.

    Args:
        search_query_ja: Japanese keyword(s) to query on Mercari/Buyee.
        timeout_seconds: Request timeout in seconds (default 4.0s).
        client: Optional pre-configured httpx.AsyncClient (useful for mocking/testing).

    Returns:
        ScrapingResult: Aggregated lowest/median price and representative thumbnail.

    Raises:
        ScrapingError: When network fails, status is non-200, or no listings are found.
    """
    clean_query = search_query_ja.strip() if search_query_ja else ""
    if not clean_query:
        raise ScrapingError("Search query cannot be empty.")

    encoded_keyword = urllib.parse.quote(clean_query)
    search_url = f"{BUYEE_MERCARI_BASE_URL}?keyword={encoded_keyword}"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        if client:
            response = await client.get(search_url, headers=headers, timeout=timeout_seconds)
        else:
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as async_http_client:
                response = await async_http_client.get(search_url, headers=headers)

        if response.status_code != 200:
            logger.error(f"Buyee returned HTTP status {response.status_code} for query: {clean_query}")
            raise ScrapingError(f"Buyee scraping failed with status code {response.status_code}")

        listings = parse_buyee_html(response.text, max_items=5)

        if not listings:
            logger.warning(f"No listings found on Buyee for query: {clean_query}")
            raise ScrapingError(f"No listings found on Buyee for query: '{clean_query}'")

        prices = [item.price_jpy for item in listings]
        lowest_price = min(prices)
        median_price = statistics.median(prices)

        # Select first valid representative image URL
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

    except ScrapingError:
        raise
    except httpx.TimeoutException as exc:
        logger.error(f"Scraping timeout for query '{clean_query}': {exc}")
        raise ScrapingError("Network request to Japanese proxy marketplace timed out.") from exc
    except httpx.RequestError as exc:
        logger.error(f"HTTP network error while scraping '{clean_query}': {exc}")
        raise ScrapingError(f"Network error connecting to Buyee: {exc}") from exc
    except Exception as exc:
        logger.error(f"Unexpected error while scraping Buyee: {exc}", exc_info=True)
        raise ScrapingError(f"Failed to scrape marketplace: {exc}") from exc
