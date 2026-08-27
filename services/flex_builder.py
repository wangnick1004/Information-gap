import logging
import urllib.parse
from typing import Any, Dict, Optional

from services.parser import ParsedAnimeItem, ParsedItem
from services.pricing import PricingResult
from services.scraper import ScrapingResult

logger = logging.getLogger("line_bot.flex_builder")

DEFAULT_PLACEHOLDER_IMAGE = (
    "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=600&auto=format&fit=crop&q=80"
)

BUYEE_GREEN_COLOR = "#06C755"  # LINE / Buyee standard vibrant green


def append_affiliate_id(url: str, affiliate_id: Optional[str]) -> str:
    """Append affiliate tracking ID to the destination URL."""
    if not url:
        return url
    if not affiliate_id or not affiliate_id.strip():
        return url

    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed.query)
    # Buyee affiliate parameter
    query_params["af"] = [affiliate_id.strip()]
    new_query = urllib.parse.urlencode(query_params, doseq=True)

    return urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))


def build_keyword_flex_message(
    japanese_keyword: str,
    search_url: str,
    affiliate_id: Optional[str] = None,
    item_title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construct a LINE Flex Message bubble for generated Japanese search keywords.

    Structure:
    - Header: Text indicating success, "🎯 日拍關鍵字生成成功！" (Bold).
    - Body: Displays "搜尋關鍵字：\n{japanese_keyword}" prominently.
    - Footer: Primary style (green) button with text "前往 Buyee 尋寶" pointing to Buyee search URL.

    Args:
        japanese_keyword: The generated Japanese search query string.
        search_url: The direct search URL to Buyee.
        affiliate_id: Optional affiliate tracking ID.
        item_title: Optional item title or brand description.

    Returns:
        Dict[str, Any]: LINE Flex Bubble JSON dictionary.
    """
    clean_keyword = japanese_keyword.strip() if japanese_keyword else "商品搜尋"
    final_buyee_url = append_affiliate_id(search_url, affiliate_id)

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
                    "action": {
                        "type": "uri",
                        "label": "前往 Buyee 尋寶",
                        "uri": final_buyee_url,
                    },
                }
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
) -> Dict[str, Any]:
    """
    Construct a rich LINE Flex Message (Bubble Container) comparing FB and Japanese market prices.

    Args:
        parsed_item: Parsed brand, model, and merch metadata.
        pricing_result: Landed cost calculation and markup analysis.
        scraper_result: Scraped JPY prices and representative image.
        affiliate_id: Optional affiliate tracking ID.

    Returns:
        Dict[str, Any]: LINE Messaging API compatible Flex Bubble structure.
    """
    image_url = scraper_result.representative_image_url or DEFAULT_PLACEHOLDER_IMAGE
    final_buyee_url = append_affiliate_id(scraper_result.search_url, affiliate_id)

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
                    "text": f"搜尋關鍵字：\n{parsed_item.search_query_ja}",
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
                    "action": {
                        "type": "uri",
                        "label": "前往 Buyee 尋寶",
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "text",
                    "text": "由 LINE 比價小幫手即時估算",
                    "size": "xxs",
                    "color": "#CCCCCC",
                    "align": "center",
                },
            ],
        },
    }

    return bubble
