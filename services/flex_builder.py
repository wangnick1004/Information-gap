import logging
import os
import re
import urllib.parse
from typing import Any, Dict, Optional

from config import settings
from services.parser import ParsedAnimeItem, ParsedItem
from services.pricing import PricingResult
from services.scraper import (
    ScrapingResult,
    normalize_rakuten_search_keyword,
    normalize_search_keyword,
)

logger = logging.getLogger("line_bot.flex_builder")

DEFAULT_PLACEHOLDER_IMAGE = (
    "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=600&auto=format&fit=crop&q=80"
)

BUYEE_GREEN_COLOR = "#06C755"        # LINE / Buyee standard vibrant green
YAHOO_AUCTIONS_COLOR = "#6F42C1"      # Yahoo! Japan Auctions distinctive purple
RAKUTEN_RED_COLOR = "#BF0000"         # Rakuten Japan signature crimson red
SHOPEE_ORANGE_COLOR = "#EE4D2D"      # Shopee official vibrant orange
TAOBAO_RED_ORANGE_COLOR = "#FF5000"  # Taobao official warm red-orange
YAHOO_TW_PURPLE_COLOR = "#6001D2"     # Yahoo! Taiwan Shopping signature purple

BUYEE_MERCARI_SEARCH_BASE_URL = "https://buyee.jp/mercari/search"
BUYEE_YAHOO_SEARCH_BASE_URL = "https://buyee.jp/item/search/query"
BUYEE_RAKUTEN_SEARCH_BASE_URL = "https://buyee.jp/rakuten/shopping/search/category/0"
SHOPEE_SEARCH_BASE_URL = "https://shopee.tw/search"
TAOBAO_SEARCH_BASE_URL = "https://ai.taobao.com/search/index.htm"
YAHOO_TW_SEARCH_BASE_URL = "https://tw.buy.yahoo.com/search/product"


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
    keyword_zh: str,
    taobao_affiliate_base_url: Optional[str] = None,
) -> str:
    """
    Construct Ai Taobao (affiliate-friendly) search URL for the given Traditional Chinese keyword.
    Base format: https://ai.taobao.com/search/index.htm?key=<keyword_zh>
    If taobao_affiliate_base_url is provided (or configured in environment/settings),
    wraps the target Ai Taobao search URL with URL-encoding into the redirect tracking format:
    '{taobao_affiliate_base_url}?t={url_encoded_taobao_search_url}'.
    """
    clean_keyword = normalize_search_keyword(keyword_zh)
    encoded = urllib.parse.quote(clean_keyword)
    base_search_url = f"{TAOBAO_SEARCH_BASE_URL}?key={encoded}"

    redirect_base = (
        taobao_affiliate_base_url
        or getattr(settings, "taobao_affiliate_base_url", None)
        or os.getenv("TAOBAO_AFFILIATE_BASE_URL")
    )
    if redirect_base and redirect_base.strip():
        base_clean = redirect_base.strip()
        encoded_target = urllib.parse.quote(base_search_url, safe="")
        separator = "&" if "?" in base_clean else "?"
        return f"{base_clean}{separator}t={encoded_target}"

    return base_search_url


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


def build_keyword_flex_message(
    japanese_keyword: str,
    search_url: str,
    affiliate_id: Optional[str] = None,
    item_title: Optional[str] = None,
    affiliate_base_url: Optional[str] = None,
    keyword_zh: Optional[str] = None,
    shopee_affiliate_base_url: Optional[str] = None,
    taobao_affiliate_base_url: Optional[str] = None,
    yahoo_tw_affiliate_base_url: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construct a LINE Flex Carousel containing 2 cards:
    - Card 1 (Japan Focus): Buyee Mercari, Buyee Yahoo Auctions, Buyee Rakuten
    - Card 2 (Greater China Focus): Shopee Taiwan, Taobao, Yahoo Taiwan
    """
    clean_keyword = normalize_search_keyword(japanese_keyword) or "商品搜尋"
    final_buyee_url = append_affiliate_id(search_url, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)
    yahoo_url = build_buyee_yahoo_search_url(clean_keyword, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)
    rakuten_url = build_buyee_rakuten_search_url(clean_keyword, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)

    zh_kw = normalize_search_keyword(keyword_zh) if keyword_zh else (item_title or clean_keyword)
    shopee_url = build_shopee_search_url(zh_kw, shopee_affiliate_base_url=shopee_affiliate_base_url)
    taobao_url = build_taobao_search_url(zh_kw, taobao_affiliate_base_url=taobao_affiliate_base_url)
    yahoo_tw_url = build_yahoo_tw_search_url(zh_kw, yahoo_tw_affiliate_base_url=yahoo_tw_affiliate_base_url)

    logger.info(
        f"[Keyword Flex URLs Constructed]\n"
        f"  Buyee Mercari: {final_buyee_url}\n"
        f"  Buyee Yahoo:   {yahoo_url}\n"
        f"  Buyee Rakuten: {rakuten_url}\n"
        f"  Shopee:        {shopee_url}\n"
        f"  Taobao:        {taobao_url}\n"
        f"  Yahoo TW:      {yahoo_tw_url}"
    )
    print(
        f"[DEBUG] [Keyword Flex URLs Constructed]\n"
        f"  Buyee Mercari: {final_buyee_url}\n"
        f"  Buyee Yahoo:   {yahoo_url}\n"
        f"  Buyee Rakuten: {rakuten_url}\n"
        f"  Shopee:        {shopee_url}\n"
        f"  Taobao:        {taobao_url}\n"
        f"  Yahoo TW:      {yahoo_tw_url}",
        flush=True,
    )

    hero_img = image_url or DEFAULT_PLACEHOLDER_IMAGE

    # Card 1: Japan Focus
    card_japan: Dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F0FFF4",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "🇯🇵 日本精選平台",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#16A34A",
                }
            ],
        },
        "hero": {
            "type": "image",
            "url": hero_img,
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover",
            "action": {
                "type": "uri",
                "label": "前往 Mercari",
                "uri": final_buyee_url,
            },
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "搜尋關鍵字 (日文)：",
                    "size": "xs",
                    "color": "#666666",
                },
                {
                    "type": "text",
                    "text": clean_keyword,
                    "weight": "bold",
                    "size": "lg",
                    "color": "#111111",
                    "wrap": True,
                },
                *(
                    [
                        {
                            "type": "text",
                            "text": f"辨識商品：{item_title.strip()}",
                            "size": "xs",
                            "color": "#888888",
                            "wrap": True,
                            "margin": "xs",
                        }
                    ]
                    if item_title and item_title.strip()
                    else []
                ),
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": BUYEE_GREEN_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 Mercari (直購)",
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_AUCTIONS_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 日本雅虎 (競標)",
                        "uri": yahoo_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": RAKUTEN_RED_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 日本樂天 (全新品)",
                        "uri": rakuten_url,
                    },
                },
            ],
        },
    }

    # Card 2: Greater China / Taiwan Focus
    card_china: Dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FFF7ED",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "🇹🇼/🇨🇳 綜合網購平台",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#EA580C",
                }
            ],
        },
        "hero": {
            "type": "image",
            "url": hero_img,
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover",
            "action": {
                "type": "uri",
                "label": "前往 台灣蝦皮",
                "uri": shopee_url,
            },
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "搜尋關鍵字 (中文)：",
                    "size": "xs",
                    "color": "#666666",
                },
                {
                    "type": "text",
                    "text": zh_kw,
                    "weight": "bold",
                    "size": "lg",
                    "color": "#111111",
                    "wrap": True,
                },
                *(
                    [
                        {
                            "type": "text",
                            "text": f"辨識商品：{item_title.strip()}",
                            "size": "xs",
                            "color": "#888888",
                            "wrap": True,
                            "margin": "xs",
                        }
                    ]
                    if item_title and item_title.strip()
                    else []
                ),
                {
                    "type": "text",
                    "text": "💡 支援台灣蝦皮、淘寶與台灣 Yahoo 比價，快速比對現貨價！",
                    "size": "xxs",
                    "color": "#999999",
                    "wrap": True,
                    "margin": "md",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": SHOPEE_ORANGE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 台灣蝦皮",
                        "uri": shopee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": TAOBAO_RED_ORANGE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 淘寶",
                        "uri": taobao_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_TW_PURPLE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 台灣 Yahoo",
                        "uri": yahoo_tw_url,
                    },
                },
            ],
        },
    }

    return {
        "type": "carousel",
        "contents": [card_japan, card_china],
    }


# Alias for convenience
build_keyword_flex = build_keyword_flex_message
def build_price_comparison_flex(
    parsed_item: ParsedItem,
    pricing_result: PricingResult,
    scraper_result: ScrapingResult,
    affiliate_id: Optional[str] = None,
    affiliate_base_url: Optional[str] = None,
    shopee_affiliate_base_url: Optional[str] = None,
    taobao_affiliate_base_url: Optional[str] = None,
    yahoo_tw_affiliate_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construct a rich LINE Flex Carousel comparing FB and cross-border market prices across:
    - Card 1 (Japan Focus): Buyee Mercari, Buyee Yahoo Auctions, Buyee Rakuten
    - Card 2 (Greater China Focus): Shopee Taiwan, Taobao, Yahoo Taiwan
    """
    image_url = scraper_result.representative_image_url or DEFAULT_PLACEHOLDER_IMAGE
    final_buyee_url = append_affiliate_id(scraper_result.search_url, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)

    jp_kw = parsed_item.keyword_jp or parsed_item.search_query_ja or f"{parsed_item.franchise} {parsed_item.character}".strip()
    clean_jp_kw = normalize_search_keyword(jp_kw) or "商品搜尋"
    yahoo_url = build_buyee_yahoo_search_url(clean_jp_kw, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)
    rakuten_url = build_buyee_rakuten_search_url(clean_jp_kw, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)

    shopee_kw = (
        parsed_item.keyword_zh
        or f"{parsed_item.franchise} {parsed_item.character}".strip()
        or parsed_item.keyword_jp
        or parsed_item.search_query_ja
    )
    clean_zh_kw = normalize_search_keyword(shopee_kw) or clean_jp_kw
    shopee_url = build_shopee_search_url(clean_zh_kw, shopee_affiliate_base_url=shopee_affiliate_base_url)
    taobao_url = build_taobao_search_url(clean_zh_kw, taobao_affiliate_base_url=taobao_affiliate_base_url)
    yahoo_tw_url = build_yahoo_tw_search_url(clean_zh_kw, yahoo_tw_affiliate_base_url=yahoo_tw_affiliate_base_url)

    logger.info(
        f"[Price Comparison Flex URLs Constructed]\n"
        f"  Buyee Mercari: {final_buyee_url}\n"
        f"  Buyee Yahoo:   {yahoo_url}\n"
        f"  Buyee Rakuten: {rakuten_url}\n"
        f"  Shopee:        {shopee_url}\n"
        f"  Taobao:        {taobao_url}\n"
        f"  Yahoo TW:      {yahoo_tw_url}"
    )
    print(
        f"[DEBUG] [Price Comparison Flex URLs Constructed]\n"
        f"  Buyee Mercari: {final_buyee_url}\n"
        f"  Buyee Yahoo:   {yahoo_url}\n"
        f"  Buyee Rakuten: {rakuten_url}\n"
        f"  Shopee:        {shopee_url}\n"
        f"  Taobao:        {taobao_url}\n"
        f"  Yahoo TW:      {yahoo_tw_url}",
        flush=True,
    )

    # Format values for display
    fb_price_str = (
        f"NT$ {int(pricing_result.fb_price_twd):,}"
        if pricing_result.fb_price_twd is not None
        else "未提供標價"
    )
    landed_cost_str = f"NT$ {int(pricing_result.landed_cost_twd):,}"
    jpy_price_str = f"¥ {int(pricing_result.price_jpy):,} JPY"

    # Determine markup status badge
    if pricing_result.is_overpriced:
        badge_bg_color = "#FFEAEA"
        badge_text_color = "#D32F2F"
        badge_title = "🔴 溢價過高警告"
        diff_val = int(pricing_result.price_difference_twd or 0)
        badge_desc = f"比日本落地價高出約 NT$ {diff_val:,} (+{int((diff_val / pricing_result.landed_cost_twd) * 100)}%)"
    elif pricing_result.fb_price_twd is not None:
        badge_bg_color = "#E8F8F0"
        badge_text_color = "#2E7D32"
        badge_title = "🟢 價格合理 / 推薦入手"
        badge_desc = "賣家開價接近或低於日本預估落地價！"
    else:
        badge_bg_color = "#EBF3FB"
        badge_text_color = "#1976D2"
        badge_title = "ℹ️ 參考日本即時行情"
        badge_desc = f"採計 Buyee Mercari 前 {scraper_result.total_found} 筆中位數"

    # Card 1: Japan Focus
    card_japan: Dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F0FFF4",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "🇯🇵 日本精選平台",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#16A34A",
                }
            ],
        },
        "hero": {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover",
            "action": {
                "type": "uri",
                "label": "前往 Mercari",
                "uri": final_buyee_url,
            },
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": f"搜尋關鍵字：\n{clean_jp_kw}",
                    "weight": "bold",
                    "size": "md",
                    "wrap": True,
                    "color": "#111111",
                },
                {
                    "type": "text",
                    "text": f"{parsed_item.franchise} {parsed_item.character}".strip() or "商品比價",
                    "size": "sm",
                    "wrap": True,
                    "color": "#444444",
                },
                {
                    "type": "text",
                    "text": f"類型：{parsed_item.item_type}" if parsed_item.item_type else "類型：商品分類",
                    "size": "xs",
                    "color": "#666666",
                    "wrap": True,
                },
                # Status Badge Container
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": badge_bg_color,
                    "cornerRadius": "8px",
                    "paddingAll": "10px",
                    "spacing": "xs",
                    "contents": [
                        {
                            "type": "text",
                            "text": badge_title,
                            "weight": "bold",
                            "size": "sm",
                            "color": badge_text_color,
                        },
                        {
                            "type": "text",
                            "text": badge_desc,
                            "size": "xxs",
                            "color": badge_text_color,
                            "wrap": True,
                        },
                    ],
                },
                {"type": "separator", "margin": "md"},
                # Price Comparison Grid
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        # FB Price Row
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "FB 賣家標價",
                                    "size": "sm",
                                    "color": "#555555",
                                    "flex": 4,
                                    "align": "start",
                                },
                                {
                                    "type": "text",
                                    "text": fb_price_str,
                                    "size": "sm",
                                    "weight": "bold",
                                    "color": "#D32F2F" if pricing_result.is_overpriced else "#111111",
                                    "align": "end",
                                    "flex": 6,
                                },
                            ],
                        },
                        # Landed Cost Row
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "預估日本落地價",
                                    "size": "sm",
                                    "color": "#111111",
                                    "weight": "bold",
                                    "flex": 4,
                                    "align": "start",
                                },
                                {
                                    "type": "text",
                                    "text": landed_cost_str,
                                    "size": "md",
                                    "weight": "bold",
                                    "color": "#00897B",
                                    "align": "end",
                                    "flex": 6,
                                },
                            ],
                        },
                        # JPY Base Price Row
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "日本行情 (中位數)",
                                    "size": "xs",
                                    "color": "#888888",
                                    "flex": 4,
                                    "align": "start",
                                },
                                {
                                    "type": "text",
                                    "text": jpy_price_str,
                                    "size": "xs",
                                    "color": "#666666",
                                    "align": "end",
                                    "flex": 6,
                                },
                            ],
                        },
                    ],
                },
                # Breakdown Footnote
                {
                    "type": "text",
                    "text": f"💡 落地價包含：匯率 {pricing_result.exchange_rate} + 代購費 NT${int(pricing_result.proxy_fee_twd)} + 預估運費 NT${int(pricing_result.shipping_twd)}",
                    "size": "xxs",
                    "color": "#AAAAAA",
                    "wrap": True,
                    "margin": "xs",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": BUYEE_GREEN_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 Mercari (直購)",
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_AUCTIONS_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 日本雅虎 (競標)",
                        "uri": yahoo_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": RAKUTEN_RED_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 日本樂天 (全新品)",
                        "uri": rakuten_url,
                    },
                },
                {
                    "type": "text",
                    "text": "由 LINE 比價小幫手即時估算",
                    "size": "xxs",
                    "color": "#CCCCCC",
                    "align": "center",
                    "margin": "xs",
                },
            ],
        },
    }

    # Card 2: Greater China / Taiwan Focus
    card_china: Dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FFF7ED",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "🇹🇼/🇨🇳 綜合網購平台",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#EA580C",
                }
            ],
        },
        "hero": {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover",
            "action": {
                "type": "uri",
                "label": "前往 台灣蝦皮",
                "uri": shopee_url,
            },
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": f"搜尋關鍵字 (中文)：\n{clean_zh_kw}",
                    "weight": "bold",
                    "size": "md",
                    "wrap": True,
                    "color": "#111111",
                },
                {
                    "type": "text",
                    "text": f"{parsed_item.franchise} {parsed_item.character}".strip() or "商品比價",
                    "size": "sm",
                    "wrap": True,
                    "color": "#444444",
                },
                {
                    "type": "text",
                    "text": "💡 支援台灣蝦皮、淘寶與台灣 Yahoo 比價，快速比對現貨價！",
                    "size": "xs",
                    "color": "#777777",
                    "wrap": True,
                    "margin": "sm",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": SHOPEE_ORANGE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 台灣蝦皮",
                        "uri": shopee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": TAOBAO_RED_ORANGE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 淘寶",
                        "uri": taobao_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_TW_PURPLE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 台灣 Yahoo",
                        "uri": yahoo_tw_url,
                    },
                },
                {
                    "type": "text",
                    "text": "由 LINE 比價小幫手即時估算",
                    "size": "xxs",
                    "color": "#CCCCCC",
                    "align": "center",
                    "margin": "xs",
                },
            ],
        },
    }

    return {
        "type": "carousel",
        "contents": [card_japan, card_china],
    }
