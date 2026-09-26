"""
比價流程入口：輸入文字或圖片，輸出不含 LINE 格式的結構化比價結果。

AI 解析器、平台抓價函式、時鐘與快取皆可由呼叫端替換（測試與評測共用同一入口）。
"""

import asyncio
import dataclasses
import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

from price_fetcher import fetch_mercari_api_price, fetch_shopee_api_price
from services.cache import TTLCache, search_cache
from services.parser import IrrelevantPostError, ParsedItem, parse_fb_post
from services.pricing import (
    PricingResult,
    calculate_dynamic_platform_prices,
    calculate_landed_cost,
)
from services.scraper import (
    ScrapingError,
    ScrapingResult,
    ScrapingTimeoutError,
    normalize_search_keyword,
    scrape_buyee_prices,
    search_chinese_platforms,
    search_taiwanese_platforms,
)
# 須在 services.scraper 之後匯入（兩模組互相匯入）
from services.lightweight_fetcher import fetch_rakuten_min_price

logger = logging.getLogger("line_bot.comparison")

CACHE_TTL_SECONDS = 3600.0
PLATFORM_TIMEOUT_SECONDS = 8.0

# Gemini Vision Model Prompt for Image Messages (Strict E-commerce Extraction Rule)
GEMINI_VISION_PROMPT = (
    "你現在是一位頂級的跨國網購商品鑑定專家。請分析這張圖片，並精準辨識出圖片中的『主體商品』。\n"
    "執行步驟：\n"
    "1. 放大檢視圖片中的任何文字、Logo、標籤或型號（啟動 OCR）。\n"
    "2. 忽略背景與人物，只專注於商品本身。\n"
    "3. 如果是動漫公仔，請找出『角色名稱＋作品名稱』。如果是 3C、相機或運動用品，請找出『品牌＋精確型號』。\n"
    "4. 【絕對限制】：請『只』輸出最精確的商品搜尋關鍵字（例如：'Fujifilm X100V 黑色' 或 '薩爾達傳說 王國之淚 林克 Amiibo'），絕對不要輸出完整的句子或描述性廢話。"
)


class PlatformStatus(str, Enum):
    OK = "ok"
    NO_MATCH = "no_match"
    TIMEOUT = "timeout"
    FAILED = "failed"
    # 未查詢，或查了但不採用其價格：只提供搜尋連結
    LINK_ONLY = "link_only"


@dataclass(frozen=True)
class PlatformQuote:
    status: PlatformStatus
    min_price_twd: Optional[int] = None
    # 價格為「起」價（例如 Mercari API 回傳的最低起標價）
    is_lower_bound: bool = False


@dataclass(frozen=True)
class ComparisonResult:
    query_text: Optional[str]
    product_name: str
    keyword_zh: str
    keyword_jp: str
    search_url: str
    platforms: Dict[str, PlatformQuote]
    fetched_at: datetime
    from_cache: bool = False
    min_price_twd: Optional[int] = None
    avg_price_twd: Optional[int] = None
    # AI 解析結果；AI 判定輸入與購物無關時為 None
    parsed_item: Optional[ParsedItem] = None
    # 過渡欄位：舊版卡片（落地價、Buyee 中位數）所需，只在 Buyee 抓價成功（完整比價）時存在；
    # 卡片改版後應移除

    pricing: Optional[PricingResult] = None
    scraper_result: Optional[ScrapingResult] = None

    @property
    def is_full(self) -> bool:
        return self.scraper_result is not None


PriceFetcher = Callable[..., Awaitable[Any]]


@dataclass
class PlatformFetchers:
    buyee: PriceFetcher = field(default=scrape_buyee_prices)
    taiwanese: PriceFetcher = field(default=search_taiwanese_platforms)
    chinese: PriceFetcher = field(default=search_chinese_platforms)
    rakuten: PriceFetcher = field(default=fetch_rakuten_min_price)
    mercari: PriceFetcher = field(default=fetch_mercari_api_price)
    shopee: PriceFetcher = field(default=fetch_shopee_api_price)


Parser = Callable[..., Awaitable[ParsedItem]]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def comparison_cache_key(text: str) -> str:
    return f"comparison:{normalize_search_keyword(text)}"


def _positive_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and value > 0 else None


def _status_of(outcome: Any, price: Optional[int]) -> PlatformStatus:
    if price is not None:
        return PlatformStatus.OK
    if isinstance(outcome, (ScrapingTimeoutError, asyncio.TimeoutError)):
        return PlatformStatus.TIMEOUT
    if isinstance(outcome, BaseException):
        return PlatformStatus.FAILED
    return PlatformStatus.NO_MATCH


def _quote(outcome: Any, price: Optional[int] = None, is_lower_bound: bool = False) -> PlatformQuote:
    return PlatformQuote(
        status=_status_of(outcome, price),
        min_price_twd=price,
        is_lower_bound=is_lower_bound and price is not None,
    )


async def compare_prices(
    *,
    text: Optional[str] = None,
    image: Optional[bytes] = None,
    parser: Optional[Parser] = None,
    fetchers: Optional[PlatformFetchers] = None,
    clock: Optional[Clock] = None,
    cache: Optional[TTLCache] = None,
) -> ComparisonResult:
    """
    比價流程入口。text 與 image 必須恰好給一個。

    AI 服務錯誤（GeminiServerError / GeminiRateLimitError / GeminiAPIError）會原樣拋出，
    由呼叫端決定如何回覆；平台抓價失敗與「與購物無關」則回傳僅含搜尋連結的結果。
    """
    if (text is None) == (image is None):
        raise ValueError("compare_prices requires exactly one of text or image")

    parser = parser or parse_fb_post
    fetchers = fetchers or PlatformFetchers()
    clock = clock or _utc_now
    cache = search_cache if cache is None else cache

    cache_key = comparison_cache_key(text) if text else None
    if cache_key:
        cached = cache.get(cache_key)
        if isinstance(cached, ComparisonResult):
            logger.info(f"⚡ [Cache Hit] Returning cached comparison for query: '{text}'")
            return dataclasses.replace(cached, from_cache=True)

    result = await _run_comparison(text, image, parser, fetchers, clock)

    if cache_key:
        cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
    return result


async def _run_comparison(
    text: Optional[str],
    image: Optional[bytes],
    parser: Parser,
    fetchers: PlatformFetchers,
    clock: Clock,
) -> ComparisonResult:
    parsed_item: Optional[ParsedItem] = None
    outcomes: Dict[str, Any] = {}

    try:
        parsed_item = await parser(
            post_text=text,
            image_data=image,
            vision_prompt=GEMINI_VISION_PROMPT if image else None,
        )

        # Silent Execution: Directly use perfected_keyword across all regional searches
        keyword_zh = (
            parsed_item.perfected_keyword
            or parsed_item.keyword_zh
            or f"{parsed_item.franchise} {parsed_item.character}"
        ).strip()
        keyword_jp = (
            parsed_item.search_query_ja
            or parsed_item.keyword_jp
            or keyword_zh
        ).strip()
        logger.info(
            f"⚡ [Silent Auto-Correction] Searching TW/JP/CN with perfected keyword: '{keyword_zh}' (JP: '{keyword_jp}')"
        )

        names = ["buyee", "taiwanese", "chinese", "rakuten", "mercari", "shopee"]
        gathered = await asyncio.gather(
            fetchers.buyee(keyword_jp, max_items=15),
            fetchers.taiwanese(keyword_zh),
            fetchers.chinese(keyword_zh),
            fetchers.rakuten(keyword_jp, timeout_seconds=PLATFORM_TIMEOUT_SECONDS),
            fetchers.mercari(
                keyword_jp,
                timeout_seconds=PLATFORM_TIMEOUT_SECONDS,
                estimated_min_usd=parsed_item.estimated_min_usd,
            ),
            fetchers.shopee(
                keyword_zh,
                timeout_seconds=PLATFORM_TIMEOUT_SECONDS,
                estimated_min_usd=parsed_item.estimated_min_usd,
            ),
            return_exceptions=True,
        )
        outcomes = dict(zip(names, gathered))
        for name in ("taiwanese", "chinese"):
            if isinstance(outcomes[name], Exception):
                logger.warning(f"{name} search failed: {outcomes[name]}")

        scraper_result = outcomes["buyee"]
        # If Buyee scraper raised an exception, route to fallback while preserving already fetched prices
        if isinstance(scraper_result, BaseException):
            raise scraper_result

        return _full_result(text, parsed_item, keyword_zh, keyword_jp, scraper_result, outcomes, clock())

    except (ScrapingError, IrrelevantPostError) as exc:
        logger.warning(f"Scraping/Parsing fallback ({type(exc).__name__}): {exc}")
        return await _links_only_result(text, parsed_item, exc, outcomes, fetchers, clock)


def _full_result(
    text: Optional[str],
    parsed_item: ParsedItem,
    keyword_zh: str,
    keyword_jp: str,
    scraper_result: ScrapingResult,
    outcomes: Dict[str, Any],
    fetched_at: datetime,
) -> ComparisonResult:
    fb_price = float(parsed_item.fb_price_twd) if parsed_item.fb_price_twd is not None else None
    pricing = calculate_landed_cost(price_jpy=scraper_result.median_price_jpy, fb_price_twd=fb_price)

    # Dynamic Price Calculation (outlier removal, 1.5% overseas conversion, min/avg range)
    tw_prices = getattr(outcomes["taiwanese"], "sample_prices", [])
    dynamic = calculate_dynamic_platform_prices(
        platform_raw_prices={
            "mercari": scraper_result.sample_prices,
            "shopee": tw_prices,
            "yahoo_tw": tw_prices,
            "taobao": getattr(outcomes["chinese"], "sample_prices", []),
        },
    )

    mercari_api_price = _positive_int(outcomes["mercari"])
    rakuten_price = _positive_int(outcomes["rakuten"])
    shopee_price = _positive_int(outcomes["shopee"])

    platforms = {
        "mercari": _quote(
            outcomes["mercari"],
            mercari_api_price or dynamic.mercari_min_price,
            is_lower_bound=mercari_api_price is not None,
        ),
        "yahoo_jp": PlatformQuote(status=PlatformStatus.LINK_ONLY),
        "rakuten": _quote(outcomes["rakuten"], rakuten_price),
        # 蝦皮只採用 API 價格（卡片上的蝦皮價格一向只顯示 API 結果）
        "shopee": _quote(outcomes["shopee"], shopee_price),
        "yahoo_tw": _quote(outcomes["taiwanese"], dynamic.yahoo_tw_min_price),
        "taobao": _quote(outcomes["chinese"], dynamic.taobao_min_price),
    }

    return ComparisonResult(
        query_text=text,
        product_name=keyword_zh,
        keyword_zh=keyword_zh,
        keyword_jp=keyword_jp,
        search_url=scraper_result.search_url,
        platforms=platforms,
        fetched_at=fetched_at,
        min_price_twd=dynamic.min_price,
        avg_price_twd=dynamic.avg_price,
        parsed_item=parsed_item,
        pricing=pricing,
        scraper_result=scraper_result,
    )


async def _links_only_result(
    text: Optional[str],
    parsed_item: Optional[ParsedItem],
    exc: Exception,
    outcomes: Dict[str, Any],
    fetchers: PlatformFetchers,
    clock: Clock,
) -> ComparisonResult:
    keyword_zh = (
        (parsed_item.perfected_keyword or parsed_item.keyword_zh)
        if parsed_item and (parsed_item.perfected_keyword or parsed_item.keyword_zh)
        else (text[:30] if text else "熱門商品")
    )
    keyword_jp = (parsed_item.keyword_jp or parsed_item.search_query_ja) if parsed_item else "人気商品"
    search_url = (
        getattr(exc, "search_url", None)
        or f"https://buyee.jp/mercari/search?keyword={urllib.parse.quote(keyword_jp)}"
    )
    product_name = (
        (parsed_item.perfected_keyword or f"{parsed_item.franchise} {parsed_item.character}").strip()
        if parsed_item and (parsed_item.perfected_keyword or parsed_item.franchise or parsed_item.character)
        else keyword_zh
    )

    # Only re-fetch platforms whose price was not already retrieved concurrently above
    estimated_min_usd = parsed_item.estimated_min_usd if parsed_item else None
    retries: Dict[str, Awaitable[Any]] = {}
    if _positive_int(outcomes.get("rakuten")) is None:
        retries["rakuten"] = fetchers.rakuten(keyword_jp, timeout_seconds=PLATFORM_TIMEOUT_SECONDS)
    if _positive_int(outcomes.get("mercari")) is None:
        retries["mercari"] = fetchers.mercari(
            keyword_jp, timeout_seconds=PLATFORM_TIMEOUT_SECONDS, estimated_min_usd=estimated_min_usd
        )
    if _positive_int(outcomes.get("shopee")) is None:
        retries["shopee"] = fetchers.shopee(
            keyword_zh, timeout_seconds=PLATFORM_TIMEOUT_SECONDS, estimated_min_usd=estimated_min_usd
        )
    if retries:
        retried = await asyncio.gather(*retries.values(), return_exceptions=True)
        outcomes = {**outcomes, **dict(zip(retries.keys(), retried))}

    def api_quote(name: str, is_lower_bound: bool = False) -> PlatformQuote:
        outcome = outcomes.get(name)
        return _quote(outcome, _positive_int(outcome), is_lower_bound=is_lower_bound)

    platforms = {
        "mercari": api_quote("mercari", is_lower_bound=True),
        "yahoo_jp": PlatformQuote(status=PlatformStatus.LINK_ONLY),
        "rakuten": api_quote("rakuten"),
        "shopee": api_quote("shopee"),
        # 僅連結模式不採用台灣 Yahoo / 淘寶的抓價結果
        "yahoo_tw": PlatformQuote(status=PlatformStatus.LINK_ONLY),
        "taobao": PlatformQuote(status=PlatformStatus.LINK_ONLY),
    }

    return ComparisonResult(
        query_text=text,
        product_name=product_name,
        keyword_zh=keyword_zh,
        keyword_jp=keyword_jp,
        search_url=search_url,
        platforms=platforms,
        fetched_at=clock(),
        parsed_item=parsed_item,
    )
