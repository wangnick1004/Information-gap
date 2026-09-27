"""
比價流程入口：輸入文字或圖片，輸出不含 LINE 格式的結構化比價結果。

AI 解析器、平台集合、時鐘與快取皆可由呼叫端替換（測試與評測共用同一入口）。
AI 判斷商品類別後，依類別對照表（services.categories）從平台集合選出 6 個平台，
只透過這些平台的轉接器查詢。

時間預算：自收到訊息起 15 秒內一定要能回覆。AI 解析有自己的上限（含重試），
平台查詢在剩餘時間內盡量等全部完成；時間一到，未完成的平台標記為逾時（卡片上只給連結）。
結果中的平台依相符最低價由低到高排序，無價格者依對照表順序排在後面；分潤只影響連結。

AI 故障（伺服器錯誤、速率限制、額度用完、逾時）時降級：以使用者原始文字作為關鍵字，
只列出「其他」類 6 個平台的搜尋連結（不查價、不快取），結果標記 ai_unavailable。
"""

import asyncio
import dataclasses
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Dict, Iterable, Mapping, Optional, Set, Tuple

from services.cache import TTLCache, search_cache
from services.categories import Category, platforms_for
from services.clock import Clock, system_clock
from services.parser import (
    DEFAULT_VISION_PROMPT,
    AI_BUSY_MESSAGE,
    GeminiAPIError,
    GeminiServerError,
    IrrelevantPostError,
    ParsedItem,
    parse_fb_post,
)
from services.platforms import FetchResult, FetchStatus, Platform, build_platforms, search_safely
from services.pricing import (
    PricingResult,
    calculate_dynamic_platform_prices,
    calculate_landed_cost,
)
from services.scraper import ScrapingResult, normalize_search_keyword

logger = logging.getLogger("line_bot.comparison")

CACHE_TTL_SECONDS = 3600.0
# 自收到訊息起，一定要在這個時間內回覆
DEADLINE_SECONDS = 15.0
# 保留給組卡片與送出 LINE 回覆的時間；平台查詢最晚在 DEADLINE_SECONDS - REPLY_MARGIN_SECONDS 截止
REPLY_MARGIN_SECONDS = 1.0
# AI 解析（含重試與等待）的上限。單次實測約 2 秒（thinking low）；解析器第一次最多等 4 秒，
# 失敗或逾時後用剩下的時間重試一次（見 services.parser.parse_fb_post 的 total_timeout_seconds）。
# 解析最慢在第 7 秒結束，平台查詢至少還有 15 - 1 - 7 = 7 秒。
AI_PARSE_BUDGET_SECONDS = 7.0
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
    # AI 解析結果；AI 判定輸入與購物無關或 AI 故障時為 None
    parsed_item: Optional[ParsedItem] = None
    # AI 故障：以原始輸入提供搜尋連結，沒有查價
    ai_unavailable: bool = False
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


class AiTimeoutError(GeminiServerError):
    """AI 解析超過時間上限；與 AI 忙碌採同一種降級回覆。"""

    def __init__(self, message: str = AI_BUSY_MESSAGE) -> None:
        super().__init__(message)


Parser = Callable[..., Awaitable[ParsedItem]]

_STATUS_BY_FETCH = {
    FetchStatus.NO_RESULTS: PlatformStatus.NO_MATCH,
    FetchStatus.TIMEOUT: PlatformStatus.TIMEOUT,
}


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
    received_at: Optional[float] = None,
) -> ComparisonResult:
    """
    比價流程入口。text 與 image 必須恰好給一個。
    platforms 為候選平台池（預設為 build_platforms() 的全部平台），實際查詢哪 6 個由類別決定。
    received_at 為收到訊息時的 clock.monotonic()，15 秒截止由此起算；未給則從現在起算。

    文字輸入遇到 AI 服務錯誤（GeminiAPIError 各子類，含超過時間上限的 AiTimeoutError）時，
    回傳 ai_unavailable 的降級結果；圖片輸入沒有可用的關鍵字，AI 服務錯誤原樣拋出由呼叫端回覆。
    平台失敗、逾時與「與購物無關」則反映在各平台狀態中。
    """
    if (text is None) == (image is None):
        raise ValueError("compare_prices requires exactly one of text or image")

    parser = parser or parse_fb_post
    platforms = build_platforms() if platforms is None else platforms
    clock = clock or system_clock
    cache = search_cache if cache is None else cache
    started = clock.monotonic() if received_at is None else received_at

    cache_key = comparison_cache_key(text) if text else None
    if cache_key:
        cached = cache.get(cache_key)
        if isinstance(cached, ComparisonResult):
            logger.info(
                f"⚡ [Cache Hit] Returning cached comparison for query: '{text}' "
                f"(fetched at {cached.fetched_at.isoformat()})"
            )
            return dataclasses.replace(cached, from_cache=True)

    deadline = started + DEADLINE_SECONDS - REPLY_MARGIN_SECONDS
    result = await _run_comparison(text, image, parser, platforms, clock, deadline)
    logger.info(
        f"[Comparison] cache miss, {_format_ms(clock.monotonic() - started)} since received; "
        + ", ".join(f"{name}={quote.status.value}" for name, quote in result.platforms.items())
    )

    # 有平台逾時或 AI 故障的結果不快取，避免一次失敗讓之後一小時都只拿到連結
    timed_out = any(q.status is PlatformStatus.TIMEOUT for q in result.platforms.values())
    if cache_key and not timed_out and not result.ai_unavailable:
        cache.set(cache_key, result, ttl=CACHE_TTL_SECONDS)
    return result


async def _run_comparison(
    text: Optional[str],
    image: Optional[bytes],
    parser: Parser,
    platforms: Mapping[str, Platform],
    clock: Clock,
    deadline: float,
) -> ComparisonResult:
    try:
        parsed_item = await _parse(text, image, parser, clock, deadline)
    except GeminiAPIError as exc:
        if text is None:
            raise
        logger.warning(f"[AI Unavailable] {type(exc).__name__}: {exc}; replying with search links for raw input")
        return _links_only_result(text, platforms, clock)

    keyword_zh, keyword_jp = _keywords(text, parsed_item)
    category = parsed_item.category if parsed_item else Category.OTHER
    selected = _select_platforms(category, platforms)
    logger.info(
        f"⚡ [Search Keywords] zh: '{keyword_zh}' / jp: '{keyword_jp}' / "
        f"category: {category.value} -> {', '.join(selected)}"
    )
    keywords = {"zh": keyword_zh, "ja": keyword_jp}

    fetched = await _search_platforms(selected, keywords, clock, deadline)

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
        platforms=_sorted_by_price(quotes),
        fetched_at=clock.now(),
        category=category,
        parsed_item=parsed_item,
    )
    mercari = fetched.get("mercari")
    if parsed_item and mercari and mercari.status is FetchStatus.OK and mercari.listings[0].currency == "JPY":
        result = _with_legacy_card_fields(result, parsed_item, mercari, keyword_jp)
    return result


def _select_platforms(category: Category, platforms: Mapping[str, Platform]) -> Dict[str, Platform]:
    """依類別對照表從平台集合選出要呈現的平台（保持對照表順序）。"""
    return {name: platforms[name] for name in platforms_for(category) if name in platforms}


def _links_only_result(text: str, platforms: Mapping[str, Platform], clock: Clock) -> ComparisonResult:
    """AI 故障時的降級結果：原始輸入當關鍵字，「其他」類 6 平台只給搜尋連結。"""
    selected = _select_platforms(Category.OTHER, platforms)
    return ComparisonResult(
        query_text=text,
        product_name=text,
        keyword_zh=text,
        keyword_jp=text,
        platforms={
            name: PlatformQuote(PlatformStatus.LINK_ONLY, search_url=platform.search_url(text))
            for name, platform in selected.items()
        },
        fetched_at=clock.now(),
        category=Category.OTHER,
        ai_unavailable=True,
    )


async def _parse(
    text: Optional[str],
    image: Optional[bytes],
    parser: Parser,
    clock: Clock,
    deadline: float,
) -> Optional[ParsedItem]:
    """AI 解析，限時 AI_PARSE_BUDGET_SECONDS（且不超過整體截止）。與購物無關時回傳 None。"""
    started = clock.monotonic()
    task = asyncio.ensure_future(
        parser(post_text=text, image_data=image, vision_prompt=GEMINI_VISION_PROMPT if image else None)
    )
    finished = await _wait_until(clock, [task], min(started + AI_PARSE_BUDGET_SECONDS, deadline))
    elapsed = _format_ms(clock.monotonic() - started)
    if task not in finished:
        logger.warning(f"[AI Parse] timed out after {elapsed}")
        raise AiTimeoutError()
    try:
        parsed_item = task.result()
    except IrrelevantPostError as exc:
        logger.warning(f"[AI Parse] irrelevant to shopping in {elapsed}, replying with search links: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"[AI Parse] failed in {elapsed}: {type(exc).__name__}")
        raise
    logger.info(f"[AI Parse] done in {elapsed}")
    return parsed_item


async def _wait_until(clock: Clock, tasks: Iterable["asyncio.Future"], deadline: float) -> Set["asyncio.Future"]:
    """
    等到所有工作完成或到達截止時間（clock.monotonic()），回傳截止前已完成的工作；
    未完成者一律取消，不再等待。
    """
    tasks = list(tasks)
    finished: Set["asyncio.Future"] = set()
    if tasks and not all(task.done() for task in tasks):
        timer = asyncio.ensure_future(clock.sleep(max(0.0, deadline - clock.monotonic())))
        all_done = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait({timer, all_done}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # 呼叫端被取消時也要收掉所有工作，不留下無人等待的查詢
            finished = {task for task in tasks if task.done()}
            timer.cancel()
            all_done.cancel()
            for task in tasks:
                task.cancel()
        return finished
    return {task for task in tasks if task.done()}


def _format_ms(elapsed: float) -> str:
    return f"{elapsed * 1000:.0f}ms"


def _keywords(text: Optional[str], parsed_item: Optional[ParsedItem]) -> Tuple[str, str]:
    fallback_zh = text[:30] if text else "熱門商品"
    if parsed_item is None:
        return fallback_zh, "人気商品"
    keyword_zh = (
        parsed_item.perfected_keyword
        or parsed_item.keyword_zh
        or f"{parsed_item.franchise} {parsed_item.character}"
    ).strip() or fallback_zh
    keyword_jp = (parsed_item.search_query_ja or parsed_item.keyword_jp).strip()
    if not keyword_jp:
        # 解析器已確保有日文關鍵字；走到這裡代表換了別的解析器，日本平台只能用中文查
        logger.warning(f"[Search Keywords] no Japanese keyword, Japanese platforms use Chinese '{keyword_zh}'")
        keyword_jp = keyword_zh
    return keyword_zh, keyword_jp


async def _search_platforms(
    platforms: Mapping[str, Platform],
    keywords: Mapping[str, str],
    clock: Clock,
    deadline: float,
) -> Dict[str, FetchResult]:
    """並行查詢有轉接器的平台，最晚等到 deadline；未完成者為逾時。"""
    started = clock.monotonic()
    timeout = max(0.0, deadline - started)
    finished_at: Dict[str, float] = {}

    async def search(name: str, platform: Platform) -> FetchResult:
        fetched = await search_safely(platform.adapter, keywords[platform.keyword_lang], timeout)
        finished_at[name] = clock.monotonic()
        return fetched

    tasks = {
        name: asyncio.ensure_future(search(name, platform))
        for name, platform in platforms.items()
        if platform.adapter is not None
    }
    finished = await _wait_until(clock, tasks.values(), deadline)

    results: Dict[str, FetchResult] = {}
    for name, task in tasks.items():
        if task in finished:
            fetched = task.result()
            elapsed = finished_at[name] - started
        else:
            fetched = FetchResult(FetchStatus.TIMEOUT, detail="unfinished at the reply deadline")
            elapsed = clock.monotonic() - started
        logger.info(
            f"[Platform] {name}: {fetched.status.value} in {_format_ms(elapsed)} "
            f"({len(fetched.listings)} listings) {fetched.detail}".rstrip()
        )
        results[name] = fetched
    return results


def _sorted_by_price(quotes: Mapping[str, PlatformQuote]) -> Dict[str, PlatformQuote]:
    """依相符最低價由低到高；無價格者依原順序（對照表順序）排在後面。只看價格，不看分潤。"""
    ranked = sorted(
        quotes.items(),
        key=lambda item: (item[1].min_price_twd is None, item[1].min_price_twd or 0),
    )
    return dict(ranked)


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
