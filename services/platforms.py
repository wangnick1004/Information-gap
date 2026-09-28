"""
平台轉接器與平台集合。

每個轉接器實作同一介面：給定關鍵字與逾時 → 回傳商品清單或明確的失敗狀態，不拋出例外。
比價流程入口只透過 build_platforms() 產生的平台集合查詢，不直接呼叫個別平台。
付費第三方 API（RapidAPI）轉接器只在評測模式（EVALUATION_MODE）下啟用。
"""

import asyncio
import json
import logging
import re
import statistics
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

import aiohttp
from bs4 import BeautifulSoup

from config import settings
from price_fetcher import get_active_blacklist, get_shopee_active_blacklist
from services.pricing import convert_to_twd, remove_outliers
from services.scraper import (
    DEFAULT_HEADERS,
    get_aiohttp_session,
    normalize_rakuten_search_keyword,
    normalize_search_keyword,
)
from services.search_links import (
    BUYEE_MERCARI_SEARCH_BASE_URL,
    BUYEE_RAKUTEN_SEARCH_BASE_URL,
    SHOPEE_SEARCH_BASE_URL,
    append_affiliate_id,
    build_buyee_rakuten_search_url,
    build_buyee_yahoo_search_url,
    build_momo_search_url,
    build_pchome_search_url,
    build_ruten_search_url,
    build_shopee_search_url,
    build_taobao_search_url,
    build_yahoo_tw_search_url,
)

logger = logging.getLogger("line_bot.platforms")

BUYEE_BASE_URL = "https://buyee.jp"
MERCARI_RAPIDAPI_URL = "https://fashion-resale-api.p.rapidapi.com/search"
MERCARI_RAPIDAPI_HOST = "fashion-resale-api.p.rapidapi.com"
SHOPEE_RAPIDAPI_URL = "https://ninjaapi2.p.rapidapi.com/api/products?shop_id=fe_amart"
SHOPEE_RAPIDAPI_HOST = "ninjaapi2.p.rapidapi.com"
USD_TWD_RATE = 32.5


class FetchStatus(str, Enum):
    OK = "ok"
    NO_RESULTS = "no_results"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    MALFORMED = "malformed"
    FAILED = "failed"


@dataclass(frozen=True)
class Listing:
    title: str
    price: float
    currency: str
    url: str
    thumbnail_url: Optional[str] = None


@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    listings: Tuple[Listing, ...] = ()
    detail: str = ""

    @classmethod
    def of(cls, listings: Sequence[Listing]) -> "FetchResult":
        return cls(FetchStatus.OK, tuple(listings)) if listings else cls(FetchStatus.NO_RESULTS)


class PlatformAdapter(Protocol):
    async def search(self, keyword: str, timeout: float) -> FetchResult: ...


# (url, headers, timeout) -> (HTTP 狀態碼, 內容)；測試以錄下的真實回應替換
HttpGet = Callable[[str, Mapping[str, str], float], Awaitable[Tuple[int, str]]]


async def aiohttp_get(url: str, headers: Mapping[str, str], timeout: float) -> Tuple[int, str]:
    session = await get_aiohttp_session()
    client_timeout = aiohttp.ClientTimeout(total=timeout, connect=min(timeout, 3.0))
    async with session.get(url, headers=dict(headers), timeout=client_timeout) as resp:
        return resp.status, await resp.text()


class _Malformed(Exception):
    """回應內容無法解析成商品清單。"""


class _RateLimited(Exception):
    """回應內容表示額度用盡或被限流。"""


async def _fetch(
    http: HttpGet,
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    parse: Callable[[str], FetchResult],
) -> FetchResult:
    """共用的請求流程：把網路錯誤、阻擋與解析失敗都轉成失敗狀態。"""
    try:
        status, body = await http(url, headers, timeout)
    except asyncio.TimeoutError:
        return FetchResult(FetchStatus.TIMEOUT, detail=f"timed out after {timeout}s")
    except Exception as exc:
        return FetchResult(FetchStatus.FAILED, detail=f"{type(exc).__name__}: {exc}")

    if status in (202, 403, 429):
        return FetchResult(FetchStatus.BLOCKED, detail=f"HTTP {status}")
    if status != 200:
        return FetchResult(FetchStatus.FAILED, detail=f"HTTP {status}")
    try:
        return parse(body)
    except _RateLimited as exc:
        return FetchResult(FetchStatus.BLOCKED, detail=f"quota exceeded: {exc}")
    except _Malformed as exc:
        return FetchResult(FetchStatus.MALFORMED, detail=str(exc))
    except Exception as exc:
        return FetchResult(FetchStatus.MALFORMED, detail=f"{type(exc).__name__}: {exc}")


async def search_safely(adapter: PlatformAdapter, keyword: str, timeout: float) -> FetchResult:
    """以硬性逾時呼叫轉接器；轉接器若仍拋出例外，也轉成失敗狀態。"""
    try:
        return await asyncio.wait_for(adapter.search(keyword, timeout=timeout), timeout=timeout)
    except asyncio.TimeoutError:
        return FetchResult(FetchStatus.TIMEOUT, detail=f"timed out after {timeout}s")
    except Exception as exc:
        logger.exception("Platform adapter raised unexpectedly")
        return FetchResult(FetchStatus.FAILED, detail=f"{type(exc).__name__}: {exc}")


def _price_number(text: str) -> Optional[float]:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return float(match.group().replace(",", "")) if match else None


def _absolute_url(href: Optional[str], base: str = BUYEE_BASE_URL) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    return urllib.parse.urljoin(base, href)


def _parse_html(body: str) -> BeautifulSoup:
    if "<" not in (body or ""):
        raise _Malformed("response is not HTML")
    return BeautifulSoup(body, "html.parser")


def _parse_cards(cards: List[Any], parse_card: Callable[[Any], Optional[Listing]]) -> FetchResult:
    listings = [listing for listing in map(parse_card, cards) if listing is not None]
    if cards and not listings:
        raise _Malformed(f"{len(cards)} item cards but none could be parsed")
    return FetchResult.of(listings)


class BuyeeMercariAdapter:
    """
    經 Buyee 搜尋 Mercari（HTML）。

    2026-09-27 起正式環境停用（工作票 21）：Buyee 對 /mercari/* 啟用 AWS WAF 的 JavaScript 挑戰
    （x-amzn-waf-action: challenge），程式請求一律拿到 HTTP 202 空頁，不繞過對方防爬機制。
    保留本轉接器與測試；Buyee 解除挑戰後，在 build_platforms() 把 mercari 的轉接器改回本類別即可恢復。
    """

    def __init__(self, http: Optional[HttpGet] = None):
        self._http = http or aiohttp_get

    async def search(self, keyword: str, timeout: float) -> FetchResult:
        url = f"{BUYEE_MERCARI_SEARCH_BASE_URL}?keyword={urllib.parse.quote(normalize_search_keyword(keyword))}"
        return await _fetch(self._http, url, DEFAULT_HEADERS, timeout, self.parse)

    @staticmethod
    def parse(body: str) -> FetchResult:
        soup = _parse_html(body)
        return _parse_cards(soup.select("ul.item-lists li.list"), BuyeeMercariAdapter._parse_card)

    @staticmethod
    def _parse_card(card: Any) -> Optional[Listing]:
        link, name, price = card.select_one("a[href]"), card.select_one(".name"), card.select_one(".price")
        amount = _price_number(price.get_text()) if price else None
        if not link or not amount:
            return None
        image = card.select_one("img")
        return Listing(
            title=name.get_text(strip=True) if name else "",
            price=amount,
            currency="JPY",
            url=_absolute_url(link["href"]),
            thumbnail_url=_absolute_url(image.get("src")) if image and image.get("src") else None,
        )


class BuyeeRakutenAdapter:
    """經 Buyee 搜尋日本樂天（HTML）。"""

    def __init__(self, http: Optional[HttpGet] = None):
        self._http = http or aiohttp_get

    async def search(self, keyword: str, timeout: float) -> FetchResult:
        query = urllib.parse.quote(normalize_rakuten_search_keyword(keyword))
        url = f"{BUYEE_RAKUTEN_SEARCH_BASE_URL}?query={query}"
        return await _fetch(self._http, url, DEFAULT_HEADERS, timeout, self.parse)

    @staticmethod
    def parse(body: str) -> FetchResult:
        soup = _parse_html(body)
        return _parse_cards(soup.select("li.product_whole"), BuyeeRakutenAdapter._parse_card)

    @staticmethod
    def _parse_card(card: Any) -> Optional[Listing]:
        link = card.select_one("a[href*='/rakuten/detail/']")
        title, price = card.select_one(".product_title"), card.select_one(".product_price")
        # 價格欄第一段是日圓（後面的 <span> 是台幣換算）
        amount = _price_number(price.find(string=True) or "") if price else None
        if not link or not amount:
            return None
        image = card.select_one("img[data-src]")
        return Listing(
            title=title.get_text(strip=True) if title else "",
            price=amount,
            currency="JPY",
            url=_absolute_url(link["href"]),
            thumbnail_url=image["data-src"] if image else None,
        )


def _load_json(body: str) -> Any:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise _Malformed(f"invalid JSON: {exc}") from exc
    if isinstance(data, dict):
        message = str(data.get("message", "")).lower()
        if any(term in message for term in ("rate limit", "quota exceeded", "too many requests")):
            raise _RateLimited(message)
    return data


class MercariRapidApiAdapter:
    """Fashion Resale API（RapidAPI，付費；僅評測模式）。只保留 platform 為 mercari 的商品。"""

    def __init__(self, api_key: Optional[str], http: Optional[HttpGet] = None):
        self._api_key = api_key
        self._http = http or aiohttp_get

    async def search(self, keyword: str, timeout: float) -> FetchResult:
        if not self._api_key:
            return FetchResult(FetchStatus.FAILED, detail="RAPIDAPI_KEY is not configured")
        params = urllib.parse.urlencode({"q": keyword.strip(), "platform": "mercari"})
        headers = {
            "Accept": "application/json",
            "x-rapidapi-host": MERCARI_RAPIDAPI_HOST,
            "x-rapidapi-key": self._api_key,
        }
        return await _fetch(self._http, f"{MERCARI_RAPIDAPI_URL}?{params}", headers, timeout, self.parse)

    @staticmethod
    def parse(body: str) -> FetchResult:
        data = _load_json(body)
        items = data.get("listings") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise _Malformed("no listings array")
        listings = []
        for item in items:
            if not isinstance(item, dict) or str(item.get("platform", "")).lower() != "mercari":
                continue
            amount = _price_number(str(item.get("price", "")))
            if not amount:
                continue
            images = item.get("images") or []
            listings.append(Listing(
                title=str(item.get("title") or ""),
                price=amount,
                currency=str(item.get("currency") or "USD").upper(),
                url=str(item.get("url") or ""),
                thumbnail_url=images[0] if images else None,
            ))
        return FetchResult.of(listings)


class ShopeeRapidApiAdapter:
    """蝦皮 RapidAPI（付費；僅評測模式）。沿用既有的請求方式與回應格式判讀。"""

    def __init__(self, api_key: Optional[str], url: Optional[str] = None, http: Optional[HttpGet] = None):
        self._api_key = api_key
        self._url = url or SHOPEE_RAPIDAPI_URL
        self._http = http or aiohttp_get

    async def search(self, keyword: str, timeout: float) -> FetchResult:
        if not self._api_key:
            return FetchResult(FetchStatus.FAILED, detail="RAPIDAPI_KEY_SHOPEE is not configured")
        separator = "&" if "?" in self._url else "?"
        url = f"{self._url}{separator}{urllib.parse.urlencode({'shop_id': keyword.strip()})}"
        headers = {
            "Accept": "application/json",
            "x-rapidapi-host": SHOPEE_RAPIDAPI_HOST,
            "x-rapidapi-key": self._api_key,
        }
        return await _fetch(self._http, url, headers, timeout, lambda body: self.parse(body, keyword))

    @staticmethod
    def parse(body: str, keyword: str = "") -> FetchResult:
        data = _load_json(body)
        items = ShopeeRapidApiAdapter._items(data)
        if items is None:
            raise _Malformed("no items array")
        listings = []
        for item in items:
            if not isinstance(item, dict):
                continue
            basic = item.get("item_basic") if isinstance(item.get("item_basic"), dict) else {}
            raw_price = next(
                (v for v in (item.get("price"), item.get("current_price"), item.get("price_min"), basic.get("price")) if v),
                None,
            )
            amount = _price_number(str(raw_price)) if raw_price is not None else None
            if not amount:
                continue
            # 蝦皮原始價格單位為 1/100000 元
            if amount > 1_000_000:
                amount = amount / 100_000
            shop_id, item_id = item.get("shopid") or basic.get("shopid"), item.get("itemid") or basic.get("itemid")
            url = item.get("url") or (
                f"https://shopee.tw/product/{shop_id}/{item_id}" if shop_id and item_id
                else f"{SHOPEE_SEARCH_BASE_URL}?keyword={urllib.parse.quote(keyword)}"
            )
            listings.append(Listing(
                title=str(item.get("title") or item.get("name") or item.get("item_name") or basic.get("name") or ""),
                price=amount,
                currency="TWD",
                url=url,
            ))
        return FetchResult.of(listings)

    @staticmethod
    def _items(data: Any) -> Optional[list]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return None
        nested = data.get("data")
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            data = nested
        for key in ("items", "products", "listings", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        return None


# --- 平台價格：沿用既有各平台的雜訊剔除規則（待相符性過濾取代）---

# (商品清單, AI 預估最低價 USD, 查詢關鍵字) -> 平台最低價 TWD
PriceRule = Callable[[Sequence[Listing], Optional[int], str], Optional[int]]


def _to_twd(listing: Listing) -> float:
    rate = USD_TWD_RATE if listing.currency == "USD" else None
    return convert_to_twd(listing.price, currency=listing.currency, exchange_rate=rate)


def _to_twd_without_fee(listing: Listing) -> float:
    rate = USD_TWD_RATE if listing.currency == "USD" else None
    return convert_to_twd(listing.price, currency=listing.currency, exchange_rate=rate, overseas_fee_rate=0.0)


def lowest_price(listings: Sequence[Listing], estimated_min_usd: Optional[int], keyword: str) -> Optional[int]:
    return int(round(min(map(_to_twd, listings)))) if listings else None


def buyee_mercari_price(listings: Sequence[Listing], estimated_min_usd: Optional[int], keyword: str) -> Optional[int]:
    """前 15 筆去掉最高、最低各 20% 後的最低價。"""
    prices = remove_outliers([item.price for item in listings[:15]])
    if not prices:
        return None
    currency = listings[0].currency
    return int(round(min(convert_to_twd(p, currency=currency) for p in prices)))


def buyee_rakuten_price(listings: Sequence[Listing], estimated_min_usd: Optional[int], keyword: str) -> Optional[int]:
    """第一頁前 5 筆中、排除 300 日圓以下（空盒、單張說明書等）後的最低價。"""
    valid = [item for item in listings[:5] if item.currency != "JPY" or item.price >= 300]
    return int(round(min(map(_to_twd, valid)))) if valid else None


def blacklisted_median_price(blacklist_for: Callable[[str], List[str]]) -> PriceRule:
    """排除標題含黑名單詞的商品，再排除低於中位數 40% 與低於 AI 預估價 60% 者，取最低價。"""

    def rule(listings: Sequence[Listing], estimated_min_usd: Optional[int], keyword: str) -> Optional[int]:
        blacklist = [term.lower() for term in blacklist_for(keyword)]
        kept = [item for item in listings if not any(term in item.title.lower() for term in blacklist)]
        if not kept:
            return None
        cutoff = statistics.median(item.price for item in kept) * 0.4
        floor_twd = estimated_min_usd * 0.6 * USD_TWD_RATE if estimated_min_usd and estimated_min_usd > 0 else 0.0
        kept = [item for item in kept if item.price >= cutoff and _to_twd_without_fee(item) >= floor_twd]
        return int(round(min(map(_to_twd, kept)))) if kept else None

    return rule


# --- 平台集合 ---

@dataclass(frozen=True)
class Platform:
    name: str
    # 查詢與搜尋連結使用的關鍵字語言："ja" 或 "zh"
    keyword_lang: str
    search_url: Callable[[str], str]
    adapter: Optional[PlatformAdapter] = None
    price_rule: PriceRule = lowest_price


def _buyee_link(build: Callable[..., str]) -> Callable[[str], str]:
    return lambda keyword: build(
        normalize_search_keyword(keyword),
        affiliate_id=settings.buyee_affiliate_id,
        affiliate_base_url=settings.affiliate_base_url,
    )


def _mercari_link(keyword: str) -> str:
    url = f"{BUYEE_MERCARI_SEARCH_BASE_URL}?keyword={urllib.parse.quote(normalize_search_keyword(keyword))}"
    return append_affiliate_id(url, affiliate_id=settings.buyee_affiliate_id, affiliate_base_url=settings.affiliate_base_url)


def build_platforms(evaluation_mode: Optional[bool] = None, http: Optional[HttpGet] = None) -> Dict[str, Platform]:
    """
    候選平台池的全部平台。每次比價實際查詢哪 6 個由類別對照表（services.categories）決定。
    evaluation_mode 未指定時依 EVALUATION_MODE 設定；只有評測模式會啟用付費的 RapidAPI 轉接器。
    沒有轉接器的平台（尚未實作或已停用）只提供搜尋連結。
    """
    evaluating = settings.evaluation_mode if evaluation_mode is None else evaluation_mode
    # 正式環境的 Mercari 只給搜尋連結：Buyee Mercari 被 AWS WAF 擋下（見 BuyeeMercariAdapter）
    mercari_adapter = MercariRapidApiAdapter(settings.rapidapi_key, http=http) if evaluating else None
    shopee_adapter = (
        ShopeeRapidApiAdapter(settings.rapidapi_key_shopee, url=settings.shopee_api_url, http=http)
        if evaluating else None
    )
    return {
        "mercari": Platform(
            "mercari", "ja", _mercari_link, mercari_adapter,
            # 正式環境目前沒有轉接器，buyee_mercari_price 保留給恢復 BuyeeMercariAdapter 時使用
            blacklisted_median_price(get_active_blacklist) if evaluating else buyee_mercari_price,
        ),
        "yahoo_jp": Platform("yahoo_jp", "ja", _buyee_link(build_buyee_yahoo_search_url)),
        "rakuten": Platform(
            "rakuten", "ja", _buyee_link(build_buyee_rakuten_search_url), BuyeeRakutenAdapter(http=http),
            buyee_rakuten_price,
        ),
        "shopee": Platform(
            "shopee", "zh", lambda kw: build_shopee_search_url(normalize_search_keyword(kw)), shopee_adapter,
            blacklisted_median_price(get_shopee_active_blacklist),
        ),
        "yahoo_tw": Platform("yahoo_tw", "zh", lambda kw: build_yahoo_tw_search_url(normalize_search_keyword(kw))),
        "taobao": Platform("taobao", "zh", lambda kw: build_taobao_search_url(normalize_search_keyword(kw))),
        "pchome": Platform("pchome", "zh", build_pchome_search_url),
        "momo": Platform("momo", "zh", build_momo_search_url),
        "ruten": Platform("ruten", "zh", build_ruten_search_url),
    }
