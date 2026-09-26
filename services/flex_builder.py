import logging
import re
from typing import Any, Dict, Optional, Tuple, Union

from price_fetcher import inject_shopee_button_to_flex
from services.comparison import ComparisonResult
from services.parser import ParsedAnimeItem, ParsedItem
from services.pricing import PricingResult
from services.scraper import (
    ScrapingResult,
    normalize_search_keyword,
)
from services.search_links import (  # noqa: F401（沿用舊匯入路徑）
    BUYEE_MERCARI_SEARCH_BASE_URL,
    BUYEE_RAKUTEN_SEARCH_BASE_URL,
    BUYEE_YAHOO_SEARCH_BASE_URL,
    SHOPEE_SEARCH_BASE_URL,
    TAOBAO_SEARCH_BASE_URL,
    YAHOO_TW_SEARCH_BASE_URL,
    append_affiliate_id,
    build_buyee_rakuten_search_url,
    build_buyee_yahoo_search_url,
    build_shopee_search_url,
    build_taobao_search_url,
    build_yahoo_tw_search_url,
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


def _override_urls(platform_urls: Optional[Dict[str, str]], **urls: str) -> Tuple[str, ...]:
    """比價結果已帶各平台搜尋連結時，以其取代卡片自行組出的連結（依平台名稱對應）。"""
    platform_urls = platform_urls or {}
    return tuple(platform_urls.get(name) or url for name, url in urls.items())


def format_button_label(
    platform_name: str,
    price: Optional[Union[int, float, str]],
    default_action_label: str,
    enable_dynamic: bool = False,
) -> str:
    """
    Format button text/label according to dynamic pricing rules:
    - If price is provided and > 0: '{platform_name} (約 NT${price})'
    - If dynamic is enabled and price is None/0/empty: '{platform_name} (點擊查看)'
    - Otherwise default to default_action_label (e.g. '前往 Mercari (直購)').
    """
    has_price = price is not None and str(price).strip() != "" and str(price).strip() != "0"
    if has_price:
        try:
            num_val = float(str(price).replace(",", ""))
            if num_val <= 0:
                has_price = False
            else:
                price_str = f"{int(round(num_val))}"
        except ValueError:
            price_str = str(price).strip()
    else:
        price_str = ""

    if has_price:
        return f"{platform_name} (約 NT${price_str})"

    if enable_dynamic:
        return f"{platform_name} (點擊查看)"

    return default_action_label


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
    perfected_keyword: Optional[str] = None,
    min_price: Optional[Union[int, float, str]] = None,
    avg_price: Optional[Union[int, float, str]] = None,
    mercari_min_price: Optional[Union[int, float, str]] = None,
    yahoo_jp_min_price: Optional[Union[int, float, str]] = None,
    rakuten_min_price: Optional[Union[int, float, str]] = None,
    shopee_min_price: Optional[Union[int, float, str]] = None,
    yahoo_tw_min_price: Optional[Union[int, float, str]] = None,
    taobao_min_price: Optional[Union[int, float, str]] = None,
    platform_min_prices: Optional[Dict[str, Any]] = None,
    enable_dynamic_buttons: Optional[bool] = None,
    platform_urls: Optional[Dict[str, str]] = None,
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
    final_buyee_url, yahoo_url, rakuten_url, shopee_url, taobao_url, yahoo_tw_url = _override_urls(
        platform_urls, mercari=final_buyee_url, yahoo_jp=yahoo_url, rakuten=rakuten_url,
        shopee=shopee_url, taobao=taobao_url, yahoo_tw=yahoo_tw_url,
    )

    if platform_min_prices:
        mercari_min_price = mercari_min_price or platform_min_prices.get("mercari") or platform_min_prices.get("buyee")
        yahoo_jp_min_price = yahoo_jp_min_price or platform_min_prices.get("yahoo_jp") or platform_min_prices.get("yahoo_auctions")
        rakuten_min_price = rakuten_min_price or platform_min_prices.get("rakuten")
        shopee_min_price = shopee_min_price or platform_min_prices.get("shopee")
        yahoo_tw_min_price = yahoo_tw_min_price or platform_min_prices.get("yahoo_tw")
        taobao_min_price = taobao_min_price or platform_min_prices.get("taobao")

    is_dynamic = enable_dynamic_buttons if enable_dynamic_buttons is not None else any(
        x is not None for x in [
            min_price, avg_price, mercari_min_price, yahoo_jp_min_price,
            rakuten_min_price, shopee_min_price, yahoo_tw_min_price, taobao_min_price, platform_min_prices
        ]
    )

    hero_img = image_url or DEFAULT_PLACEHOLDER_IMAGE
    correction_text = perfected_keyword.strip() if perfected_keyword and perfected_keyword.strip() else None

    # Price range header block
    price_range_header = []
    if min_price is not None and avg_price is not None and str(min_price).strip() and str(avg_price).strip():
        fmt_min = f"{int(round(float(min_price)))}" if isinstance(min_price, (int, float)) or (isinstance(min_price, str) and min_price.replace(",", "").isdigit()) else str(min_price)
        fmt_avg = f"{int(round(float(avg_price)))}" if isinstance(avg_price, (int, float)) or (isinstance(avg_price, str) and avg_price.replace(",", "").isdigit()) else str(avg_price)
        price_range_header = [
            {
                "type": "text",
                "text": f"💰 跨國均價區間：NT$ {fmt_min} ~ NT$ {fmt_avg}",
                "size": "xs",
                "color": "#1E3A8A",
                "weight": "bold",
                "wrap": True,
                "margin": "xs",
            }
        ]

    # Resolve button labels
    mercari_btn_text = format_button_label("Mercari", mercari_min_price, "前往 Mercari (直購)", is_dynamic)
    yahoo_jp_btn_text = format_button_label("日本雅虎", yahoo_jp_min_price, "前往 日本雅虎 (競標)", is_dynamic)
    rakuten_btn_text = format_button_label("日本樂天", rakuten_min_price, "前往 日本樂天 (全新品)", is_dynamic)

    shopee_btn_text = format_button_label("台灣蝦皮", shopee_min_price, "前往 台灣蝦皮", is_dynamic)
    yahoo_tw_btn_text = format_button_label("台灣 Yahoo", yahoo_tw_min_price, "前往 台灣 Yahoo", is_dynamic)
    taobao_btn_text = format_button_label("淘寶", taobao_min_price, "前往 淘寶 (請手動搜尋)", is_dynamic)

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
                },
                *(
                    [
                        {
                            "type": "text",
                            "text": f"🔎 已自動為您精準鎖定：{correction_text}",
                            "size": "xs",
                            "color": "#15803D",
                            "weight": "bold",
                            "wrap": True,
                            "margin": "xs",
                        }
                    ]
                    if correction_text
                    else []
                ),
                *price_range_header,
            ],
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
                    "text": mercari_btn_text,
                    "action": {
                        "type": "uri",
                        "label": mercari_btn_text,
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_AUCTIONS_COLOR,
                    "height": "sm",
                    "text": yahoo_jp_btn_text,
                    "action": {
                        "type": "uri",
                        "label": yahoo_jp_btn_text,
                        "uri": yahoo_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": RAKUTEN_RED_COLOR,
                    "height": "sm",
                    "text": rakuten_btn_text,
                    "action": {
                        "type": "uri",
                        "label": rakuten_btn_text,
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
                },
                *(
                    [
                        {
                            "type": "text",
                            "text": f"🔎 已自動為您精準鎖定：{correction_text}",
                            "size": "xs",
                            "color": "#EA580C",
                            "weight": "bold",
                            "wrap": True,
                            "margin": "xs",
                        }
                    ]
                    if correction_text
                    else []
                ),
                *price_range_header,
            ],
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
                    "text": shopee_btn_text,
                    "action": {
                        "type": "uri",
                        "label": shopee_btn_text,
                        "uri": shopee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_TW_PURPLE_COLOR,
                    "height": "sm",
                    "text": yahoo_tw_btn_text,
                    "action": {
                        "type": "uri",
                        "label": yahoo_tw_btn_text,
                        "uri": yahoo_tw_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": TAOBAO_RED_ORANGE_COLOR,
                    "height": "sm",
                    "text": taobao_btn_text,
                    "action": {
                        "type": "uri",
                        "label": taobao_btn_text,
                        "uri": taobao_url,
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
    perfected_keyword: Optional[str] = None,
    min_price: Optional[Union[int, float, str]] = None,
    avg_price: Optional[Union[int, float, str]] = None,
    mercari_min_price: Optional[Union[int, float, str]] = None,
    yahoo_jp_min_price: Optional[Union[int, float, str]] = None,
    rakuten_min_price: Optional[Union[int, float, str]] = None,
    shopee_min_price: Optional[Union[int, float, str]] = None,
    yahoo_tw_min_price: Optional[Union[int, float, str]] = None,
    taobao_min_price: Optional[Union[int, float, str]] = None,
    platform_min_prices: Optional[Dict[str, Any]] = None,
    enable_dynamic_buttons: Optional[bool] = None,
    platform_urls: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Construct a rich LINE Flex Carousel comparing FB and cross-border market prices across:
    - Card 1 (Japan Focus): Buyee Mercari, Buyee Yahoo Auctions, Buyee Rakuten
    - Card 2 (Greater China Focus): Shopee Taiwan, Taobao, Yahoo Taiwan
    """
    image_url = scraper_result.representative_image_url or DEFAULT_PLACEHOLDER_IMAGE
    final_buyee_url = append_affiliate_id(scraper_result.search_url, affiliate_id=affiliate_id, affiliate_base_url=affiliate_base_url)

    if platform_min_prices:
        mercari_min_price = mercari_min_price or platform_min_prices.get("mercari") or platform_min_prices.get("buyee")
        yahoo_jp_min_price = yahoo_jp_min_price or platform_min_prices.get("yahoo_jp") or platform_min_prices.get("yahoo_auctions")
        rakuten_min_price = rakuten_min_price or platform_min_prices.get("rakuten")
        shopee_min_price = shopee_min_price or platform_min_prices.get("shopee")
        yahoo_tw_min_price = yahoo_tw_min_price or platform_min_prices.get("yahoo_tw")
        taobao_min_price = taobao_min_price or platform_min_prices.get("taobao")

    is_dynamic = enable_dynamic_buttons if enable_dynamic_buttons is not None else any(
        x is not None for x in [
            min_price, avg_price, mercari_min_price, yahoo_jp_min_price,
            rakuten_min_price, shopee_min_price, yahoo_tw_min_price, taobao_min_price, platform_min_prices
        ]
    )

    correction_text = (
        perfected_keyword
        or (getattr(parsed_item, "perfected_keyword", None) if parsed_item else None)
        or (getattr(parsed_item, "suggested_term", None) if parsed_item else None)
    )
    if correction_text:
        correction_text = correction_text.strip()

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
    final_buyee_url, yahoo_url, rakuten_url, shopee_url, taobao_url, yahoo_tw_url = _override_urls(
        platform_urls, mercari=final_buyee_url, yahoo_jp=yahoo_url, rakuten=rakuten_url,
        shopee=shopee_url, taobao=taobao_url, yahoo_tw=yahoo_tw_url,
    )

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

    # Price range header block
    price_range_header = []
    if min_price is not None and avg_price is not None and str(min_price).strip() and str(avg_price).strip():
        fmt_min = f"{int(round(float(min_price)))}" if isinstance(min_price, (int, float)) or (isinstance(min_price, str) and min_price.replace(",", "").isdigit()) else str(min_price)
        fmt_avg = f"{int(round(float(avg_price)))}" if isinstance(avg_price, (int, float)) or (isinstance(avg_price, str) and avg_price.replace(",", "").isdigit()) else str(avg_price)
        price_range_header = [
            {
                "type": "text",
                "text": f"💰 跨國均價區間：NT$ {fmt_min} ~ NT$ {fmt_avg}",
                "size": "xs",
                "color": "#1E3A8A",
                "weight": "bold",
                "wrap": True,
                "margin": "xs",
            }
        ]

    # Resolve button labels
    mercari_btn_text = format_button_label("Mercari", mercari_min_price, "前往 Mercari (直購)", is_dynamic)
    yahoo_jp_btn_text = format_button_label("日本雅虎", yahoo_jp_min_price, "前往 日本雅虎 (競標)", is_dynamic)
    rakuten_btn_text = format_button_label("日本樂天", rakuten_min_price, "前往 日本樂天 (全新品)", is_dynamic)

    shopee_btn_text = format_button_label("台灣蝦皮", shopee_min_price, "前往 台灣蝦皮", is_dynamic)
    yahoo_tw_btn_text = format_button_label("台灣 Yahoo", yahoo_tw_min_price, "前往 台灣 Yahoo", is_dynamic)
    taobao_btn_text = format_button_label("淘寶", taobao_min_price, "前往 淘寶 (請手動搜尋)", is_dynamic)

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
                },
                *(
                    [
                        {
                            "type": "text",
                            "text": f"🔎 已自動為您精準鎖定：{correction_text}",
                            "size": "xs",
                            "color": "#15803D",
                            "weight": "bold",
                            "wrap": True,
                            "margin": "xs",
                        }
                    ]
                    if correction_text
                    else []
                ),
                *price_range_header,
            ],
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
                    "text": mercari_btn_text,
                    "action": {
                        "type": "uri",
                        "label": mercari_btn_text,
                        "uri": final_buyee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_AUCTIONS_COLOR,
                    "height": "sm",
                    "text": yahoo_jp_btn_text,
                    "action": {
                        "type": "uri",
                        "label": yahoo_jp_btn_text,
                        "uri": yahoo_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": RAKUTEN_RED_COLOR,
                    "height": "sm",
                    "text": rakuten_btn_text,
                    "action": {
                        "type": "uri",
                        "label": rakuten_btn_text,
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
                },
                *(
                    [
                        {
                            "type": "text",
                            "text": f"🔎 已自動為您精準鎖定：{correction_text}",
                            "size": "xs",
                            "color": "#EA580C",
                            "weight": "bold",
                            "wrap": True,
                            "margin": "xs",
                        }
                    ]
                    if correction_text
                    else []
                ),
                *price_range_header,
            ],
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
                    "text": shopee_btn_text,
                    "action": {
                        "type": "uri",
                        "label": shopee_btn_text,
                        "uri": shopee_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": YAHOO_TW_PURPLE_COLOR,
                    "height": "sm",
                    "text": yahoo_tw_btn_text,
                    "action": {
                        "type": "uri",
                        "label": yahoo_tw_btn_text,
                        "uri": yahoo_tw_url,
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": TAOBAO_RED_ORANGE_COLOR,
                    "height": "sm",
                    "text": taobao_btn_text,
                    "action": {
                        "type": "uri",
                        "label": taobao_btn_text,
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

    return {
        "type": "carousel",
        "contents": [card_japan, card_china],
    }



def build_comparison_flex(result: ComparisonResult) -> Tuple[Dict[str, Any], str]:
    """
    將比價結果轉成 LINE Flex 卡片，回傳 (flex_dict, alt_text)。
    只負責呈現：平台順序與價格皆來自比價結果，分潤設定只影響連結。
    """
    common = dict(
        **{f"{name}_min_price": quote.min_price_twd for name, quote in result.platforms.items()},
        enable_dynamic_buttons=True,
        platform_urls={name: quote.search_url for name, quote in result.platforms.items()},
    )

    if result.is_full:
        flex_dict = build_price_comparison_flex(
            parsed_item=result.parsed_item,
            pricing_result=result.pricing,
            scraper_result=result.scraper_result,
            perfected_keyword=result.keyword_zh,
            min_price=result.min_price_twd,
            avg_price=result.avg_price_twd,
            **common,
        )
        alt_text = f"【比價分析】{result.keyword_zh}".strip()
    else:
        flex_dict = build_keyword_flex_message(
            japanese_keyword=result.keyword_jp,
            search_url=result.platforms["mercari"].search_url if "mercari" in result.platforms else "",
            item_title=result.product_name,
            keyword_zh=result.keyword_zh,
            perfected_keyword=result.keyword_zh if result.parsed_item else None,
            **common,
        )
        alt_text = "比價成功，來去撈便宜～"

    shopee = result.platforms.get("shopee")
    flex_dict = inject_shopee_button_to_flex(flex_dict, shopee.min_price_twd if shopee else None)
    return flex_dict, alt_text
