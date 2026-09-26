"""
比價流程入口：輸入文字或圖片，輸出不含 LINE 格式的結構化比價結果。

AI 解析器、平台集合、時鐘與快取皆可由呼叫端替換（測試與評測共用同一入口）。
AI 判斷商品類別後，依類別對照表（services.categories）從平台集合選出 6 個平台，
只透過這些平台的轉接器查詢。
"""

import asyncio
import dataclasses
import logging
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Dict, Mapping, Optional, Tuple

from services.cache import TTLCache, search_cache
from services.categories import Category, platforms_for
from services.parser import DEFAULT_VISION_PROMPT, IrrelevantPostError, ParsedItem, parse_fb_post
from services.platforms import FetchResult, FetchStatus, Platform, build_platforms, search_safely
from services.pricing import (
    PricingResult,
    calculate_dynamic_platform_prices,
    calculate_landed_cost,
)
from services.scraper import ScrapingResult, normalize_search_keyword

logger = logging.getLogger("line_bot.comparison")

CACHE_TTL_SECONDS = 3600.0
PLATFORM_TIMEOUT_SECONDS = 8.0
# 舊版卡片的 Mercari 統計只採計前 15 筆
LEGACY_CARD_SAMPLE_SIZE = 15

# 圖片輸入使用的 Gemini 提示詞（通用商品辨識）
GEMINI_VISION_PROMPT = DEFAULT_VISION_PROMPT


class PlatformStatus(str, Enum):
    OK = "ok"
    NO_MATCH = "no_match"
    TIMEOUT = "timeout"
    FAILED = "failed"
    # 尚無轉接器：只提供搜尋連結
    LINK_ONLY = "link_only"


@dataclass(frozen=True)
class PlatformQuote:
    status: PlatformStatus
    min_price_twd: Optional[int] = None
    # 平台搜尋連結（含分潤參數）
    search_url: str = ""


@dataclass(frozen=True)
class ComparisonResult:
    query_text: Optional[str]
    product_name: str
    keyword_zh: str
    keyword_jp: str
    platforms: Dict[str, PlatformQuote]
    fetched_at: datetime
    category: Category = Category.OTHER
    from_cache: bool = False
    min_price_twd: Optional[int] = None
    avg_price_twd: Optional[int] = None
    # AI 解析結果；AI 判定輸入與購物無關時為 None
    parsed_item: Optional[ParsedItem] = None
    # 過渡欄位：舊版卡片（落地價、Mercari 中位數）所需，只在 Mercari 查到日圓商品時存在；
    # 卡片改版後應移除
    pricing: Optional[PricingResult] = None
    scraper_result: Optional[ScrapingResult] = None

    @property
    def has_price(self) -> bool:
        """至少一個平台查到真實價格。"""
        return any(quote.status is PlatformStatus.OK for quote in self.platforms.values())

    @property
    def is_full(self) -> bool:
        return self.scraper_result is not None


Parser = Callable[..., Awaitable[ParsedItem]]
Clock = Callable[[], datetime]

_STATUS_BY_FETCH = {
    FetchStatus.NO_RESULTS: PlatformStatus.NO_MATCH,
    FetchStatus.TIMEOUT: PlatformStatus.TIMEOUT,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def comparison_cache_key(text: str) -> str:
    return f"comparison:{normalize_search_keyword(text)}"


async def compare_prices(
    *,
    text: Optional[str] = None,
    image: Optional[bytes] = None,
    parser: Optional[Parser] = None,
    platforms: Optional[Mapping[str, Platform]] = None,
    clock: Optional[Clock] = None,
    cache: Optional[TTLCache] = None,
) -> ComparisonResult:
    """
    比價流程入口。text 與 image 必須恰好給一個。
    platforms 為候選平台池（預設為 build_platforms() 的全部平台），實際查詢哪 6 個由類別決定。

    AI 服務錯誤（GeminiServerError / GeminiRateLimitError / GeminiAPIError）會原樣拋出，
    由呼叫端決定如何回覆；平台失敗與「與購物無關」則反映在各平台狀態中。
    """
    if (text is None) == (image is None):
        raise ValueError("compare_prices requires exactly one of text or image")

    parser = parser or parse_fb_post
    platforms = build_platforms() if platforms is None else platforms
    clock = clock or _utc_now
    cache = search_cache if cache is None else cache

    cache_key = comparison_cache_key(text) if text else None
    if cache_key:
        cached = cache.get(cache_key)
        if isinstance(cached, ComparisonResult):
            logger.info(f"⚡ [Cache Hit] Returning cached comparison for query: '{text}'")
            return dataclasses.replace(cached, from_cache=True)

    result = await _run_comparison(text, image, parser, platforms, clock)

    if cache_key:
        cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
    return result


async def _run_comparison(
    text: Optional[str],
    image: Optional[bytes],
    parser: Parser,
    platforms: Mapping[str, Platform],
    clock: Clock,
) -> ComparisonResult:
    parsed_item: Optional[ParsedItem] = None
    try:
        parsed_item = await parser(
            post_text=text,
            image_data=image,
            vision_prompt=GEMINI_VISION_PROMPT if image else None,
        )
    except IrrelevantPostError as exc:
        logger.warning(f"Input judged irrelevant to shopping, replying with search links: {exc}")

    keyword_zh, keyword_jp = _keywords(text, parsed_item)
    category = parsed_item.category if parsed_item else Category.OTHER
    selected = {name: platforms[name] for name in platforms_for(category) if name in platforms}
    logger.info(
        f"⚡ [Search Keywords] zh: '{keyword_zh}' / jp: '{keyword_jp}' / "
        f"category: {category.value} -> {', '.join(selected)}"
    )
    keywords = {"zh": keyword_zh, "ja": keyword_jp}

    fetched = await _search_platforms(selected, keywords)

    estimated_min_usd = parsed_item.estimated_min_usd if parsed_item else None
    quotes = {
        name: _quote(platform, fetched.get(name), keywords[platform.keyword_lang], estimated_min_usd)
        for name, platform in selected.items()
    }

    result = ComparisonResult(
        query_text=text,
        product_name=keyword_zh,
        keyword_zh=keyword_zh,
        keyword_jp=keyword_jp,
        platforms=quotes,
        fetched_at=clock(),
        category=category,
        parsed_item=parsed_item,
    )
    mercari = fetched.get("mercari")
    if parsed_item and mercari and mercari.status is FetchStatus.OK and mercari.listings[0].currency == "JPY":
        result = _with_legacy_card_fields(result, parsed_item, mercari, keyword_jp)
    return result


def _keywords(text: Optional[str], parsed_item: Optional[ParsedItem]) -> Tuple[str, str]:
    fallback_zh = text[:30] if text else "熱門商品"
    if parsed_item is None:
        return fallback_zh, "人気商品"
    keyword_zh = (
        parsed_item.perfected_keyword
        or parsed_item.keyword_zh
        or f"{parsed_item.franchise} {parsed_item.character}"
    ).strip() or fallback_zh
    keyword_jp = (parsed_item.search_query_ja or parsed_item.keyword_jp or keyword_zh).strip()
    return keyword_zh, keyword_jp


async def _search_platforms(
    platforms: Mapping[str, Platform], keywords: Mapping[str, str]
) -> Dict[str, FetchResult]:
    async def timed(name: str, platform: Platform) -> Tuple[str, FetchResult]:
        started = time.monotonic()
        fetched = await search_safely(platform.adapter, keywords[platform.keyword_lang], PLATFORM_TIMEOUT_SECONDS)
        logger.info(
            f"[Platform] {name}: {fetched.status.value} in {(time.monotonic() - started) * 1000:.0f}ms "
            f"({len(fetched.listings)} listings) {fetched.detail}".rstrip()
        )
        return name, fetched

    searchable = [(name, p) for name, p in platforms.items() if p.adapter is not None]
    return dict(await asyncio.gather(*(timed(name, p) for name, p in searchable)))


def _quote(
    platform: Platform,
    fetched: Optional[FetchResult],
    keyword: str,
    estimated_min_usd: Optional[int],
) -> PlatformQuote:
    search_url = platform.search_url(keyword)
    if fetched is None:
        return PlatformQuote(PlatformStatus.LINK_ONLY, search_url=search_url)
    if fetched.status is not FetchStatus.OK:
        return PlatformQuote(_STATUS_BY_FETCH.get(fetched.status, PlatformStatus.FAILED), search_url=search_url)

    price = platform.price_rule(fetched.listings, estimated_min_usd, keyword)
    status = PlatformStatus.OK if price is not None else PlatformStatus.NO_MATCH
    return PlatformQuote(status, price, search_url=search_url)


def _with_legacy_card_fields(
    result: ComparisonResult,
    parsed_item: ParsedItem,
    mercari: FetchResult,
    keyword_jp: str,
) -> ComparisonResult:
    """舊版卡片需要的 Mercari 統計（中位數落地價、代表圖、價格區間）。"""
    listings = mercari.listings[:LEGACY_CARD_SAMPLE_SIZE]
    prices = [item.price for item in listings]
    median_jpy = statistics.median(prices)
    scraper_result = ScrapingResult(
        query=normalize_search_keyword(keyword_jp),
        search_url=result.platforms["mercari"].search_url,
        lowest_price_jpy=min(prices),
        median_price_jpy=median_jpy,
        representative_image_url=next(
            (item.thumbnail_url for item in listings if (item.thumbnail_url or "").startswith("http")), None
        ),
        sample_prices=prices,
        total_found=len(prices),
    )
    fb_price = float(parsed_item.fb_price_twd) if parsed_item.fb_price_twd is not None else None
    dynamic = calculate_dynamic_platform_prices(platform_raw_prices={"mercari": prices})
    return dataclasses.replace(
        result,
        min_price_twd=dynamic.min_price,
        avg_price_twd=dynamic.avg_price,
        pricing=calculate_landed_cost(price_jpy=median_jpy, fb_price_twd=fb_price),
        scraper_result=scraper_result,
    )
