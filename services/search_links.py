"""
各平台搜尋連結（含分潤參數）的組成方式。

平台集合用它產生比價結果中的搜尋連結；分潤參數只影響連結，不影響排序。
"""

import os
import urllib.parse
from typing import Optional

from config import settings
from services.scraper import normalize_rakuten_search_keyword, normalize_search_keyword

BUYEE_MERCARI_SEARCH_BASE_URL = "https://buyee.jp/mercari/search"
BUYEE_YAHOO_SEARCH_BASE_URL = "https://buyee.jp/item/search/query"
BUYEE_RAKUTEN_SEARCH_BASE_URL = "https://buyee.jp/rakuten/shopping/search/category/0"
SHOPEE_SEARCH_BASE_URL = "https://shopee.tw/search"
TAOBAO_SEARCH_BASE_URL = "https://world.taobao.com/search/search.htm"
YAHOO_TW_SEARCH_BASE_URL = "https://tw.buy.yahoo.com/search/product"
PCHOME_SEARCH_BASE_URL = "https://24h.pchome.com.tw/search/"
MOMO_SEARCH_BASE_URL = "https://www.momoshop.com.tw/search/searchShop.jsp"
RUTEN_SEARCH_BASE_URL = "https://www.ruten.com.tw/find/"


def build_buyee_yahoo_search_url(
    keyword_jp: str,
    affiliate_id: Optional[str] = None,
    affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct Yahoo! Japan Auctions search URL via Buyee for the given Japanese keyword.
    Base format: https://buyee.jp/item/search/query/<keyword_jp>
    If affiliate_id is provided, appends '?af={affiliate_id}'.
    If affiliate_base_url is provided (or configured in environment/settings),
    wraps the target Yahoo! Auctions search URL with URL-encoding into the redirect tracking format:
    '{affiliate_base_url}?t={url_encoded_buyee_yahoo_search_url}'.
    """
    clean_keyword = normalize_search_keyword(keyword_jp)
    encoded_keyword = urllib.parse.quote(clean_keyword)
    base_search_url = f"{BUYEE_YAHOO_SEARCH_BASE_URL}/{encoded_keyword}"

    return append_affiliate_id(
        base_search_url,
        affiliate_id=affiliate_id,
        affiliate_base_url=affiliate_base_url,
    )


def build_buyee_rakuten_search_url(
    keyword_jp: str,
    affiliate_id: Optional[str] = None,
    affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct Rakuten Japan search URL via Buyee for the given Japanese keyword.
    Applies alphanumeric space removal (e.g., 'Switch 2' -> 'Switch2', 'PS 5' -> 'PS5')
    exclusively for Rakuten search indexing.
    Base format: https://buyee.jp/rakuten/shopping/search/category/0?query=<keyword_jp>
    If affiliate_id is provided, appends 'af={affiliate_id}'.
    If affiliate_base_url is provided (or configured in environment/settings),
    wraps the target Rakuten search URL with URL-encoding into the redirect tracking format:
    '{affiliate_base_url}?t={url_encoded_buyee_rakuten_search_url}'.
    """
    clean_keyword = normalize_rakuten_search_keyword(keyword_jp)
    encoded_keyword = urllib.parse.quote(clean_keyword)
    base_search_url = f"{BUYEE_RAKUTEN_SEARCH_BASE_URL}?query={encoded_keyword}"

    return append_affiliate_id(
        base_search_url,
        affiliate_id=affiliate_id,
        affiliate_base_url=affiliate_base_url,
    )


def build_shopee_search_url(
    keyword_zh: str,
    shopee_affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct Shopee Taiwan search URL for the given Traditional Chinese keyword.
    If shopee_affiliate_base_url is provided (or configured in environment/settings),
    wraps the target Shopee search URL with URL-encoding into the redirect tracking format:
    '{shopee_affiliate_base_url}?t={url_encoded_shopee_search_url}'.
    """
    clean_keyword = normalize_search_keyword(keyword_zh)
    encoded = urllib.parse.quote(clean_keyword)
    base_search_url = f"{SHOPEE_SEARCH_BASE_URL}?keyword={encoded}"

    redirect_base = (
        shopee_affiliate_base_url
        or getattr(settings, "shopee_affiliate_base_url", None)
        or os.getenv("SHOPEE_AFFILIATE_BASE_URL")
    )
    if redirect_base and redirect_base.strip():
        base_clean = redirect_base.strip()
        encoded_target = urllib.parse.quote(base_search_url, safe="")
        separator = "&" if "?" in base_clean else "?"
        return f"{base_clean}{separator}t={encoded_target}"

    return base_search_url


def build_taobao_search_url(
    keyword_zh: Optional[str] = None,
    taobao_affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct Taobao button URL.
    Due to Taobao's lack of deep-linking support for search queries behind affiliate redirects,
    this strictly returns the bare TAOBAO_AFFILIATE_BASE_URL without appending '?t=' or passing keyword_zh.
    Falls back to TAOBAO_SEARCH_BASE_URL if no affiliate base URL is configured.
    """
    redirect_base = (
        taobao_affiliate_base_url
        or getattr(settings, "taobao_affiliate_base_url", None)
        or os.getenv("TAOBAO_AFFILIATE_BASE_URL")
    )
    if redirect_base and redirect_base.strip():
        return redirect_base.strip()

    if keyword_zh:
        clean_keyword = normalize_search_keyword(keyword_zh)
        encoded = urllib.parse.quote(clean_keyword)
        return f"{TAOBAO_SEARCH_BASE_URL}?q={encoded}"

    return TAOBAO_SEARCH_BASE_URL


def build_yahoo_tw_search_url(
    keyword_zh: str,
    yahoo_tw_affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct Yahoo Taiwan search URL for the given Traditional Chinese keyword.
    Base format: https://tw.buy.yahoo.com/search/product?p=<keyword_zh>
    If yahoo_tw_affiliate_base_url is provided (or configured in environment/settings),
    wraps the target Yahoo Taiwan search URL with URL-encoding into the redirect tracking format:
    '{yahoo_tw_affiliate_base_url}?t={url_encoded_yahoo_tw_search_url}'.
    """
    clean_keyword = normalize_search_keyword(keyword_zh)
    encoded = urllib.parse.quote(clean_keyword)
    base_search_url = f"{YAHOO_TW_SEARCH_BASE_URL}?p={encoded}"

    redirect_base = (
        yahoo_tw_affiliate_base_url
        or getattr(settings, "yahoo_tw_affiliate_base_url", None)
        or os.getenv("YAHOO_TW_AFFILIATE_BASE_URL")
    )
    if redirect_base and redirect_base.strip():
        base_clean = redirect_base.strip()
        encoded_target = urllib.parse.quote(base_search_url, safe="")
        separator = "&" if "?" in base_clean else "?"
        return f"{base_clean}{separator}t={encoded_target}"

    return base_search_url


def _query_search_url(base_url: str, param: str, keyword: str) -> str:
    return f"{base_url}?{param}={urllib.parse.quote(normalize_search_keyword(keyword))}"


def build_pchome_search_url(keyword_zh: str) -> str:
    """PChome 24h 購物搜尋連結（尚無分潤參數）。"""
    return _query_search_url(PCHOME_SEARCH_BASE_URL, "q", keyword_zh)


def build_momo_search_url(keyword_zh: str) -> str:
    """momo 購物網搜尋連結（尚無分潤參數）。"""
    return _query_search_url(MOMO_SEARCH_BASE_URL, "keyword", keyword_zh)


def build_ruten_search_url(keyword_zh: str) -> str:
    """露天拍賣搜尋連結（尚無分潤參數）。"""
    return _query_search_url(RUTEN_SEARCH_BASE_URL, "q", keyword_zh)


def append_affiliate_id(
    url: str,
    affiliate_id: Optional[str] = None,
    affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct the final destination URL with affiliate tracking:
    1. If affiliate_id is provided, appends 'af={affiliate_id}' to the Buyee search URL.
    2. If affiliate_base_url is provided (or configured in environment/settings),
       wraps the target Buyee URL with URL-encoding into the redirect tracking format:
       '{affiliate_base_url}?t={url_encoded_buyee_url}'.
    """
    if not url:
        return url

    target_url = url
    if affiliate_id and affiliate_id.strip():
        parsed = urllib.parse.urlparse(target_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        # Buyee affiliate parameter
        query_params["af"] = [affiliate_id.strip()]
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        target_url = urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    base_redirect_url = affiliate_base_url or getattr(settings, "affiliate_base_url", None) or os.getenv("AFFILIATE_BASE_URL")
    if base_redirect_url and base_redirect_url.strip():
        base_clean = base_redirect_url.strip()
        encoded_target = urllib.parse.quote(target_url, safe="")
        separator = "&" if "?" in base_clean else "?"
        return f"{base_clean}{separator}t={encoded_target}"

    return target_url
