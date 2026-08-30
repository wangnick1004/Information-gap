import logging
import os
import urllib.parse
from typing import Any, Dict, Optional

from config import settings
from services.parser import ParsedAnimeItem, ParsedItem
from services.pricing import PricingResult
from services.scraper import ScrapingResult, normalize_search_keyword

logger = logging.getLogger("line_bot.flex_builder")

DEFAULT_PLACEHOLDER_IMAGE = (
    "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=600&auto=format&fit=crop&q=80"
)

BUYEE_GREEN_COLOR = "#06C755"        # LINE / Buyee standard vibrant green
SHOPEE_ORANGE_COLOR = "#EE4D2D"      # Shopee official vibrant orange
TAOBAO_RED_ORANGE_COLOR = "#FF5000"  # Taobao official warm red-orange

SHOPEE_SEARCH_BASE_URL = "https://shopee.tw/search"
TAOBAO_SEARCH_BASE_URL = "https://s.taobao.com/search"


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
    Construct Taobao search URL for the given Traditional Chinese keyword.
    If taobao_affiliate_base_url is provided (or configured in environment/settings),
    wraps the target Taobao search URL with URL-encoding into the redirect tracking format:
    '{taobao_affiliate_base_url}?t={url_encoded_taobao_search_url}'.
    """
    clean_keyword = normalize_search_keyword(keyword_zh)
    encoded = urllib.parse.quote(clean_keyword)
    base_search_url = f"{TAOBAO_SEARCH_BASE_URL}?q={encoded}"

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
) -> Dict[str, Any]:
    """
    Construct a LINE Flex Message bubble for generated Japanese search keywords with 3 marketplace buttons.

    Structure:
    - Header: Text indicating success, "比價成功，來去撈便宜～" (Bold).
    - Body: Displays "搜尋關鍵字：\n{japanese_keyword}" prominently.
    - Footer: 3 primary buttons (Buyee green, Shopee orange, Taobao red-orange).

    Args:
        japanese_keyword: The generated Japanese search query string.
        search_url: The direct search URL to Buyee.
        affiliate_id: Optional affiliate tracking ID.
        item_title: Optional item title or brand description.
        affiliate_base_url: Optional affiliate tracking base URL redirect endpoint.
        keyword_zh: Optional Traditional Chinese keyword for Shopee and Taobao.
        shopee_affiliate_base_url: Optional Shopee affiliate tracking base URL redirect endpoint.
        taobao_affiliate_base_url: Optional Taobao affiliate tracking base URL redirect endpoint.

    Returns:
        Dict[str, Any]: LINE Flex Bubble JSON dictionary.
    """
    clean_keyword = normalize_search_keyword(japanese_keyword) or "商品搜尋"
    final_buyee_url = append_affiliate_id(search_url, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)

    zh_kw = normalize_search_keyword(keyword_zh) if keyword_zh else (item_title or clean_keyword)
    shopee_url = build_shopee_search_url(zh_kw, shopee_affiliate_base_url=shopee_affiliate_base_url)
    taobao_url = build_taobao_search_url(zh_kw, taobao_affiliate_base_url=taobao_affiliate_base_url)

    body_contents = [
        {
            "type": "text",
            "text": "搜尋關鍵字：",
            "size": "sm",
            "color": "#666666",
        },
        {
            "type": "text",
            "text": clean_keyword,
            "weight": "bold",
            "size": "xl",
            "color": "#111111",
            "wrap": True,
        },
    ]

    if item_title and item_title.strip():
        body_contents.append({
            "type": "text",
            "text": f"辨識商品：{item_title.strip()}",
            "size": "xs",
            "color": "#888888",
            "wrap": True,
            "margin": "md",
        })

    if keyword_zh and keyword_zh.strip() and normalize_search_keyword(keyword_zh) != clean_keyword:
        body_contents.append({
            "type": "text",
            "text": f"中文關鍵字：{normalize_search_keyword(keyword_zh)}",
            "size": "xs",
            "color": "#888888",
            "wrap": True,
            "margin": "xs",
        })

    bubble: Dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F0FFF4",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "比價成功，來去撈便宜～",
                    "weight": "bold",
                    "size": "md",
                    "color": "#16A34A",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "20px",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": BUYEE_GREEN_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 Buyee 尋寶",
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": SHOPEE_ORANGE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 蝦皮 搜尋",
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
                        "label": "前往 淘寶 搜尋",
                        "uri": taobao_url,
                    },
                },
            ],
        },
    }

    return bubble


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
) -> Dict[str, Any]:
    """
    Construct a rich LINE Flex Message (Bubble Container) comparing FB and Japanese market prices.

    Args:
        parsed_item: Parsed brand, model, and merch metadata.
        pricing_result: Landed cost calculation and markup analysis.
        scraper_result: Scraped JPY prices and representative image.
        affiliate_id: Optional affiliate tracking ID.
        affiliate_base_url: Optional affiliate tracking base URL redirect endpoint.
        shopee_affiliate_base_url: Optional Shopee affiliate tracking base URL redirect endpoint.
        taobao_affiliate_base_url: Optional Taobao affiliate tracking base URL redirect endpoint.

    Returns:
        Dict[str, Any]: LINE Messaging API compatible Flex Bubble structure.
    """
    image_url = scraper_result.representative_image_url or DEFAULT_PLACEHOLDER_IMAGE
    final_buyee_url = append_affiliate_id(scraper_result.search_url, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)

    shopee_kw = (
        parsed_item.keyword_zh
        or f"{parsed_item.franchise} {parsed_item.character}".strip()
        or parsed_item.keyword_jp
        or parsed_item.search_query_ja
    )
    shopee_url = build_shopee_search_url(shopee_kw, shopee_affiliate_base_url=shopee_affiliate_base_url)
    taobao_url = build_taobao_search_url(shopee_kw, taobao_affiliate_base_url=taobao_affiliate_base_url)

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

    bubble: Dict[str, Any] = {
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
                    "text": "比價成功，來去撈便宜～",
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
                "label": "View Image",
                "uri": final_buyee_url,
            },
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"搜尋關鍵字：\n{parsed_item.keyword_jp or parsed_item.search_query_ja}",
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
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": BUYEE_GREEN_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 Buyee 尋寶",
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": SHOPEE_ORANGE_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "前往 蝦皮 搜尋",
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
                        "label": "前往 淘寶 搜尋",
                        "uri": taobao_url,
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

    return bubble
