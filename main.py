import asyncio
from collections import defaultdict
import logging
import os
import random
import time
import urllib.parse
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    FlexContainer,
    FlexMessage,
    MessageAction,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import FollowEvent, ImageMessageContent, MessageEvent, TextMessageContent
from mangum import Mangum
from pydantic import BaseModel
from config import Settings, settings
from services.flex_builder import (
    build_keyword_flex_message,
    build_price_comparison_flex,
)
# Alias FlexSendMessage and TextSendMessage for LINE SDK convention compatibility
FlexSendMessage = FlexMessage
TextSendMessage = TextMessage
from services.cache import search_cache
from services.parser import (
    GeminiAPIError,
    GeminiRateLimitError,
    GeminiServerError,
    IrrelevantPostError,
    ParsedItem,
    parse_fb_post,
)
from services.pricing import (
    DynamicPriceResult,
    PricingResult,
    calculate_dynamic_platform_prices,
    calculate_landed_cost,
    convert_to_twd,
    remove_outliers,
)
from services.scraper import (
    CrossBorderSearchResult,
    ScrapingBlockedError,
    ScrapingError,
    ScrapingResult,
    ScrapingTimeoutError,
    normalize_search_keyword,
    scrape_buyee_prices,
    search_all_platforms_concurrently,
    search_chinese_platforms,
    search_taiwanese_platforms,
)
from services.lightweight_fetcher import (
    construct_platform_search_url,
    fetch_lightweight_platform_min_price,
    fetch_lightweight_prices,
    fetch_mercari_min_price,
    fetch_rakuten_min_price,
    filter_extreme_low_prices,
    parse_platform_first_page_prices,
)
from price_fetcher import (
    fetch_mercari_api_price,
    fetch_price,
    inject_mercari_button_to_flex,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("line_bot")

# Rich Menu Text Responses for Non-Search Commands
GUIDE_RESPONSE_TEXT = (
    "📖 【新手指南】\n"
    "1. 直接傳送想找的商品照片\n"
    "2. 或輸入精準關鍵字\n"
    "機器人就會自動幫您找出日台比價連結喔！\n\n"
    "💡 【為什麼推薦逛日拍？】\n"
    "• 挖寶聖地：極度適合找絕版底片相機、二手 CCD、稀有桌球拍或動漫周邊，品項豐富且日人保存習慣佳。\n"
    "• 匯率與價差：搭配日幣匯率，日本直購二手品往往比台灣社團或網拍更划算。\n"
    "• 競標撿便宜：日本雅虎常有低價起標，設定好最高預算，有機會撿到夢幻逸品！\n\n"
    "⚠️ 【跨國小提醒】\n"
    "• 留意隱藏費用：下標前除了估算國際空運費，也要注意網頁有無標示收取「日本境內運費」。\n"
    "• 禁運品地雷：含有鋰電池的相機、電子設備或易燃物，購買前請務必確認集運倉的空運規範。"
)

DISCLAIMER_RESPONSE_TEXT = (
    "🎯 【創立初衷：打破資訊落差】\n"
    "很多時候，我們在社群平台上買貴了，只是因為不知道「真實的市價」。本工具致力於打破跨國網購的資訊壁壘，幫您一鍵比對海內外價格，輕鬆看穿定價不透明的亂象！\n\n"
    "📊 【各平台尋寶指南】\n"
    "• 日本平台 (Mercari/日雅虎)：適合找二手極美品、絕版相機、限量動漫周邊與運動用品。\n"
    "• 大陸平台 (淘寶)：適合買日常配件、生活收納、汽機車消耗品，具備極致性價比。\n"
    "• 台灣平台 (蝦皮/Yahoo)：適合急需現貨、需要台灣在地保固與退換貨服務的商品。\n\n"
    "⚖️ 【使用免責聲明】\n"
    "1. 本服務僅提供「關鍵字翻譯」與「比價連結彙整」，不經手任何金流或物流。\n"
    "2. 系統不保證搜尋結果的商品真偽與品質，跨國網購請自行評估賣家評價。\n"
    "3. 若產生跨境交易糾紛或退換貨問題，請直接聯繫原購物平台與集運商。"
)

SHIPPING_GUIDE_RESPONSE_TEXT = (
    "📦 【集貨倉是什麼？】\n"
    "集貨倉就像海外的「代收管理員」。能幫你把不同賣家的包裹，合併打包成一大箱寄回台灣，大幅節省國際運費！\n\n"
    "🇯🇵 【日拍集運重點 (日雅/Mercari)】\n"
    "• 材積陷阱：日本空運極重視「材積重」(體積大運費就貴)。老手必找提供「免費去外箱」的集運商來省錢。\n"
    "• 安全加固：購買絕版相機、CCD 或高價桌球拍，務必加購防撞驗貨服務。\n"
    "• 隱藏成本：下單前留意賣家有無收取「日本境內運費」。\n\n"
    "🇨🇳 【中國集運重點 (淘寶/京東)】\n"
    "• 普特貨分流：衣服是普貨；含鋰電池(如Switch手把)、藍牙或液體是「特貨」，須走專屬航班，報錯會被海關重罰！\n"
    "• 運送選擇：急用選空運(3-5天)；買機車耗材或大型傢俱選海運(7-14天)最划算。\n"
    "• 包稅服務：單次逾2000元或半年進口逾6次會被課稅，選「包稅航線」被抽到關稅將由集運商全額吸收。"
)

FEEDBACK_RESPONSE_TEXT = (
    "🛠️ 【客服與問題回報】\n"
    "哎呀，機器人出錯了嗎？或是您有任何新功能建議？\n\n"
    "請點擊下方表單告訴我們，這會幫助系統變得更好！\n"
    "👉https://forms.gle/4ACKqFQWE1xQjexG7\n\n"
    "如有緊急合作或建議，也歡迎直接來信：\n"
    "✉️weiwei33442@gmail.com"
)

WELCOME_RESPONSE_TEXT = (
    "🎉 歡迎加入！【拒絕當韭菜，消弭資訊落差】\n\n"
    "很多時候我們在社群平台上買貴了，只是因為不知道海外真實市價。本機器人專為打破跨境網購的資訊壁壘而生，幫您一鍵找出真實底價！\n\n"
    "💡 【三大核心功能】\n"
    "1️⃣ 圖片搜尋：直接傳送一張想買的商品照片，AI 會自動幫您辨識並全網比價。\n"
    "2️⃣ 關鍵字/常用語翻譯：直接輸入商品名稱（如：Switch 2、Viscaria 桌球拍、底片相機），系統會自動翻譯成最精準的日文，並同步給出日、台、中三地的比價連結。\n"
    "3️⃣ 跨境網購寶典：點擊下方六宮格選單，從海關 EZ WAY 認證、中日集運倉挑選，到各平台的優勢解析一次看懂。\n\n"
    "👇 現在，請直接點擊下方選單左上角的「一鍵尋寶體驗」，看看比價神器實際上怎麼運作吧！"
)

# Gemini Vision Model Prompt for Image Messages (Strict E-commerce Extraction Rule)
GEMINI_VISION_PROMPT = (
    "你現在是一位頂級的跨國網購商品鑑定專家。請分析這張圖片，並精準辨識出圖片中的『主體商品』。\n"
    "執行步驟：\n"
    "1. 放大檢視圖片中的任何文字、Logo、標籤或型號（啟動 OCR）。\n"
    "2. 忽略背景與人物，只專注於商品本身。\n"
    "3. 如果是動漫公仔，請找出『角色名稱＋作品名稱』。如果是 3C、相機或運動用品，請找出『品牌＋精確型號』。\n"
    "4. 【絕對限制】：請『只』輸出最精確的商品搜尋關鍵字（例如：'Fujifilm X100V 黑色' 或 '薩爾達傳說 王國之淚 林克 Amiibo'），絕對不要輸出完整的句子或描述性廢話。"
)


from contextlib import asynccontextmanager

# Predefined Hot Keywords for Background Cache Pre-warming
PREWARM_KEYWORDS = [
    "Switch 2",
    "薩爾達傳說 王國之淚",
    "咒術迴戰 五條 手辦",
    "Viscaria 桌球拍",
    "CCD 數位相機",
    "底片相機",
]


async def prewarm_search_cache() -> None:
    """
    Background pre-warming task that pre-fetches and caches search comparisons
    for popular hot keywords on app startup, ensuring instant (< 10ms) responses for users.
    """
    logger.info("🔥 [Cache Pre-warm] Starting background cache pre-warming for hot keywords...")
    for kw in PREWARM_KEYWORDS:
        try:
            cache_key = f"flex:{normalize_search_keyword(kw)}"
            if cache_key in search_cache:
                continue
            parsed_item = await parse_fb_post(post_text=kw)
            jp_task = scrape_buyee_prices(parsed_item.search_query_ja)
            tw_task = search_taiwanese_platforms(parsed_item.keyword_zh)
            cn_task = search_chinese_platforms(parsed_item.keyword_zh)
            scraper_result, tw_result, cn_result = await asyncio.gather(jp_task, tw_task, cn_task)
            pricing_result = calculate_landed_cost(price_jpy=scraper_result.median_price_jpy)
            flex_dict = build_price_comparison_flex(
                parsed_item=parsed_item,
                pricing_result=pricing_result,
                scraper_result=scraper_result,
                affiliate_id=settings.buyee_affiliate_id,
                affiliate_base_url=settings.affiliate_base_url,
                shopee_affiliate_base_url=settings.shopee_affiliate_base_url,
                taobao_affiliate_base_url=settings.taobao_affiliate_base_url,
                yahoo_tw_affiliate_base_url=settings.yahoo_tw_affiliate_base_url,
            )
            alt_text = f"【比價分析】{parsed_item.franchise} {parsed_item.character}".strip()
            search_cache.set(cache_key, {"flex_dict": flex_dict, "alt_text": alt_text}, ttl=3600.0)
            logger.info(f"🔥 [Cache Pre-warm] Successfully pre-warmed cache for: '{kw}'")
        except Exception as exc:
            logger.debug(f"Cache pre-warm skipped for '{kw}': {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle application startup and shutdown tasks."""
    prewarm_task = asyncio.create_task(prewarm_search_cache())
    yield
    if not prewarm_task.done():
        prewarm_task.cancel()


# FastAPI Application Initialization
app = FastAPI(
    title="Line E-Commerce Price Comparison Bot",
    description="LINE Bot with Gemini multimodal entity extraction and Buyee price comparison",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Memory-Based Rate Limiter (Sliding Window: 5 requests / 60 seconds per user) ---
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_COOLDOWN_MESSAGE = "⚠️ 系統冷卻中！您的搜尋頻率過高，請稍候 1 分鐘後再試。這能確保每位使用者都有順暢的比價體驗喔！"

user_request_timestamps: defaultdict[str, list[float]] = defaultdict(list)


def is_rate_limited(
    user_id: str,
    max_requests: int = RATE_LIMIT_MAX_REQUESTS,
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
) -> bool:
    """
    Check if a user has exceeded the rate limit threshold (max 5 requests per 60 seconds).
    Automatically trims timestamps older than the sliding window.
    Returns True if rate limited, False otherwise.
    """
    if not user_id:
        return False

    current_time = time.time()
    cutoff_time = current_time - window_seconds

    # Filter out timestamps older than the sliding window
    user_request_timestamps[user_id] = [
        ts for ts in user_request_timestamps[user_id] if ts > cutoff_time
    ]

    if len(user_request_timestamps[user_id]) >= max_requests:
        return True

    # Record this search query timestamp
    user_request_timestamps[user_id].append(current_time)
    return False


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@app.get("/", response_model=HealthResponse, summary="Root Health Check")
@app.get("/api/health", response_model=HealthResponse, summary="API Health Check")
async def health_check() -> HealthResponse:
    """Public health check endpoint for monitoring and deployment verification."""
    return HealthResponse(
        status="healthy",
        service="line-price-comparison-bot",
        version="1.0.0",
    )


async def handle_line_events(events: list, access_token: str) -> None:
    """
    Process incoming LINE webhook events with multimodal price comparison pipeline:
    1. Listen for FollowEvent and send comprehensive welcome guide.
    2. Check Rich Menu command router (新手指南, 平台比較與免責, 集運倉介紹, 客服與回報).
    3. Extract text or fetch image bytes via LINE Blob API.
    4. Parse entities & generate Japanese search query with Gemini.
    5. Scrape real-time prices and thumbnail from Buyee Mercari.
    6. Calculate estimated landed cost in TWD and assess markup.
    7. Generate and reply with a LINE Flex Message bubble.
    8. Graceful fallback on errors to ensure user is always notified.
    """
    if not access_token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN is not configured; skipping API reply.")
        return

    configuration = Configuration(access_token=access_token)
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        line_bot_blob_api = AsyncMessagingApiBlob(api_client)

        for event in events:
            # Handle FollowEvent (New Friend / Unblock)
            if isinstance(event, FollowEvent):
                logger.info(f"Handling FollowEvent from user {getattr(event.source, 'user_id', 'unknown')}")
                try:
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=WELCOME_RESPONSE_TEXT)],
                        )
                    )
                    logger.info("Successfully sent welcome message for FollowEvent.")
                except Exception as exc:
                    logger.error(f"Failed to send welcome message for FollowEvent: {exc}", exc_info=True)
                continue

            if not isinstance(event, MessageEvent):
                continue

            user_text: Optional[str] = None
            image_bytes: Optional[bytes] = None

            if isinstance(event.message, TextMessageContent):
                user_text = event.message.text.strip().lower()
                logger.info(f"Processing text message from user: {user_text[:60]}...")

                # --- Rich Menu Command Router ---
                # 1. Newbie Guide Command
                if user_text in ("新手指南", "新手圖解指南"):
                    logger.info("Handling '新手指南' rich menu command.")
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=GUIDE_RESPONSE_TEXT)],
                        )
                    )
                    continue

                # 2. Disclaimer & Platform Comparison Command
                if user_text in ("平台比較與免責", "法律免責聲明"):
                    logger.info("Handling '平台比較與免責' rich menu command.")
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=DISCLAIMER_RESPONSE_TEXT)],
                        )
                    )
                    continue

                # 3. Shipping Guide Command
                if user_text in ("集運倉介紹", "集貨倉介紹"):
                    logger.info("Handling '集運倉介紹' rich menu command.")
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=SHIPPING_GUIDE_RESPONSE_TEXT)],
                        )
                    )
                    continue

                # 4. Customer Support and Feedback Command
                if user_text in ("客服與回報", "客服與問題回報"):
                    logger.info("Handling '客服與回報' rich menu command.")
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=FEEDBACK_RESPONSE_TEXT)],
                        )
                    )
                    continue

            elif isinstance(event.message, ImageMessageContent):
                logger.info(f"Processing image message id={event.message.id} from user...")
                try:
                    image_bytes = await line_bot_blob_api.get_message_content(event.message.id)
                    logger.info(f"Successfully retrieved {len(image_bytes)} bytes of image content.")
                except Exception as exc:
                    logger.error(f"Failed to retrieve image blob: {exc}", exc_info=True)
                    fallback_text = "無法下載您傳送的圖片，請稍後再試或直接提供文字描述。"
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )
                    continue
            else:
                # Unsupported message type (stickers, audio, etc.)
                continue

            user_id = getattr(event.source, "user_id", None)

            # Rate Limiting Intercept: Strictly allow max 5 searches per 60 seconds per user
            if user_id and is_rate_limited(user_id):
                logger.warning(
                    f"🛑 [Rate Limit Exceeded] User {user_id} exceeded {RATE_LIMIT_MAX_REQUESTS} searches per {RATE_LIMIT_WINDOW_SECONDS}s."
                )
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=RATE_LIMIT_COOLDOWN_MESSAGE)],
                    )
                )
                continue

            # Step 0: Trigger LINE Loading Animation immediately (typing indicator for user)
            if user_id:
                try:
                    await line_bot_api.show_loading_animation(
                        ShowLoadingAnimationRequest(
                            chat_id=user_id,
                            loading_seconds=60,
                        )
                    )
                    logger.debug(f"Triggered LINE loading animation for user {user_id}")
                except Exception as anim_exc:
                    logger.debug(f"Failed to show loading animation (non-critical): {anim_exc}")

            # Step 0.5: Check 1-hour TTL Cache for exact keyword search queries
            cache_key = f"flex:{normalize_search_keyword(user_text)}" if user_text else None
            if cache_key:
                cached_data = search_cache.get(cache_key)
                if cached_data and isinstance(cached_data, dict) and "flex_dict" in cached_data:
                    logger.info(f"⚡ [Cache Hit] Instantly returning cached Flex Message for query: '{user_text}'")
                    flex_container = FlexContainer.from_dict(cached_data["flex_dict"])
                    reply_msg = FlexMessage(alt_text=cached_data.get("alt_text", "【比價分析】"), contents=flex_container)
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[reply_msg],
                        )
                    )
                    continue

            parsed_item: Optional[ParsedItem] = None

            try:
                # Step 1: Multimodal Entity Extraction & Japanese/Chinese Search Query Generation
                parsed_item = await parse_fb_post(
                    post_text=user_text,
                    image_data=image_bytes,
                    vision_prompt=GEMINI_VISION_PROMPT if image_bytes else None,
                )

                # Silent Execution: Directly use perfected_keyword across all regional searches
                effective_keyword = (
                    parsed_item.perfected_keyword
                    or parsed_item.keyword_zh
                    or f"{parsed_item.franchise} {parsed_item.character}"
                ).strip()
                effective_jp_keyword = (
                    parsed_item.search_query_ja
                    or parsed_item.keyword_jp
                    or effective_keyword
                ).strip()

                logger.info(
                    f"⚡ [Silent Auto-Correction] Searching TW/JP/CN with perfected keyword: '{effective_keyword}' (JP: '{effective_jp_keyword}')"
                )

                # Step 2: Concurrently Search Japanese, Chinese, and Taiwanese platforms simultaneously (top 15 listings)
                jp_task = scrape_buyee_prices(effective_jp_keyword, max_items=15)
                tw_task = search_taiwanese_platforms(effective_keyword)
                cn_task = search_chinese_platforms(effective_keyword)
                rakuten_task = fetch_rakuten_min_price(effective_jp_keyword, timeout_seconds=2.0)
                mercari_task = fetch_mercari_api_price(effective_jp_keyword, timeout_seconds=2.5, enable_mock=False)
                scraper_result, tw_result, cn_result, rakuten_price, mercari_api_price = await asyncio.gather(
                    jp_task, tw_task, cn_task, rakuten_task, mercari_task
                )

                # Step 3: Compute Landed Cost & Markup Analysis
                fb_price = (
                    float(parsed_item.fb_price_twd)
                    if parsed_item.fb_price_twd is not None
                    else None
                )
                pricing_result = calculate_landed_cost(
                    price_jpy=scraper_result.median_price_jpy,
                    fb_price_twd=fb_price,
                )

                # Step 4: Dynamic Price Calculation (outlier removal, 1.5% overseas conversion, min/avg range)
                platform_raw_prices = {
                    "mercari": scraper_result.sample_prices if scraper_result else [],
                    "shopee": getattr(tw_result, "sample_prices", []),
                    "yahoo_tw": getattr(tw_result, "sample_prices", []),
                    "taobao": getattr(cn_result, "sample_prices", []),
                }
                dynamic_pricing = calculate_dynamic_platform_prices(
                    platform_raw_prices=platform_raw_prices,
                )
                if rakuten_price and rakuten_price > 0:
                    dynamic_pricing.rakuten_min_price = rakuten_price
                    dynamic_pricing.platform_min_prices["rakuten"] = rakuten_price
                if mercari_api_price and mercari_api_price > 0:
                    dynamic_pricing.mercari_min_price = mercari_api_price
                    dynamic_pricing.platform_min_prices["mercari"] = mercari_api_price

                mercari_display_price = (
                    f"{mercari_api_price}起"
                    if (mercari_api_price and mercari_api_price > 0)
                    else dynamic_pricing.mercari_min_price
                )

                # Step 5: Build LINE Flex Message UI with Dynamic Price Range & Platform Minimums
                flex_dict = build_price_comparison_flex(
                    parsed_item=parsed_item,
                    pricing_result=pricing_result,
                    scraper_result=scraper_result,
                    affiliate_id=settings.buyee_affiliate_id,
                    affiliate_base_url=settings.affiliate_base_url,
                    shopee_affiliate_base_url=settings.shopee_affiliate_base_url,
                    taobao_affiliate_base_url=settings.taobao_affiliate_base_url,
                    yahoo_tw_affiliate_base_url=settings.yahoo_tw_affiliate_base_url,
                    perfected_keyword=effective_keyword,
                    min_price=dynamic_pricing.min_price,
                    avg_price=dynamic_pricing.avg_price,
                    mercari_min_price=mercari_display_price,
                    shopee_min_price=dynamic_pricing.shopee_min_price,
                    taobao_min_price=dynamic_pricing.taobao_min_price,
                    yahoo_tw_min_price=dynamic_pricing.yahoo_tw_min_price,
                    yahoo_jp_min_price=dynamic_pricing.yahoo_jp_min_price,
                    rakuten_min_price=dynamic_pricing.rakuten_min_price,
                    enable_dynamic_buttons=True,
                )
                flex_container = FlexContainer.from_dict(flex_dict)
                alt_text = f"【比價分析】{effective_keyword}".strip()

                reply_msg = FlexMessage(alt_text=alt_text, contents=flex_container)
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[reply_msg],
                    )
                )
                logger.info("Successfully replied with Flex Message comparison card.")

                # Save successful result to 1-hour TTL cache
                if cache_key:
                    search_cache.set(cache_key, {"flex_dict": flex_dict, "alt_text": alt_text}, ttl=3600.0)

            except (ScrapingTimeoutError, ScrapingBlockedError, ScrapingError, IrrelevantPostError) as exc:
                logger.warning(f"Scraping/Parsing fallback ({type(exc).__name__}): {exc}")
                fallback_kw = (
                    (parsed_item.perfected_keyword or parsed_item.keyword_zh)
                    if parsed_item and (parsed_item.perfected_keyword or parsed_item.keyword_zh)
                    else (user_text[:30] if user_text else "熱門商品")
                )
                kw_jp = (
                    parsed_item.keyword_jp or parsed_item.search_query_ja
                    if parsed_item
                    else "人気商品"
                )
                kw_zh = fallback_kw
                search_url = getattr(exc, "search_url", None) or f"https://buyee.jp/mercari/search?keyword={urllib.parse.quote(kw_jp)}"
                item_title = (
                    (parsed_item.perfected_keyword or f"{parsed_item.franchise} {parsed_item.character}").strip()
                    if parsed_item and (parsed_item.perfected_keyword or parsed_item.franchise or parsed_item.character)
                    else kw_zh
                )

                # Attempt fast lightweight fetch if applicable
                rakuten_fallback_price = None
                mercari_fallback_price = None
                try:
                    rakuten_fallback_price, mercari_fallback_price = await asyncio.gather(
                        fetch_rakuten_min_price(kw_jp, timeout_seconds=2.0),
                        fetch_mercari_api_price(kw_jp, timeout_seconds=2.5, enable_mock=False),
                        return_exceptions=True,
                    )
                    if isinstance(rakuten_fallback_price, Exception):
                        rakuten_fallback_price = None
                    if isinstance(mercari_fallback_price, Exception):
                        mercari_fallback_price = None
                except Exception:
                    rakuten_fallback_price = None
                    mercari_fallback_price = None

                keyword_flex_dict = build_keyword_flex_message(
                    japanese_keyword=kw_jp,
                    search_url=search_url,
                    affiliate_id=settings.buyee_affiliate_id,
                    item_title=item_title,
                    affiliate_base_url=settings.affiliate_base_url,
                    keyword_zh=kw_zh,
                    shopee_affiliate_base_url=settings.shopee_affiliate_base_url,
                    taobao_affiliate_base_url=settings.taobao_affiliate_base_url,
                    yahoo_tw_affiliate_base_url=settings.yahoo_tw_affiliate_base_url,
                    perfected_keyword=fallback_kw if parsed_item else None,
                    min_price=None,
                    avg_price=None,
                    mercari_min_price=f"{mercari_fallback_price}起" if mercari_fallback_price else None,
                    shopee_min_price=None,
                    taobao_min_price=None,
                    yahoo_tw_min_price=None,
                    yahoo_jp_min_price=None,
                    rakuten_min_price=rakuten_fallback_price,
                    enable_dynamic_buttons=True,
                )
                flex_container = FlexContainer.from_dict(keyword_flex_dict)
                reply_msg = FlexSendMessage(
                    alt_text="比價成功，來去撈便宜～",
                    contents=flex_container,
                )
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[reply_msg],
                    )
                )

                # Cache keyword fallback card too
                if cache_key:
                    search_cache.set(cache_key, {"flex_dict": keyword_flex_dict, "alt_text": "比價成功，來去撈便宜～"}, ttl=3600.0)

            except GeminiServerError as exc:
                logger.warning(f"Gemini server error (503 UNAVAILABLE): {exc}")
                fallback_text = str(exc) if str(exc) else "目前 AI 伺服器大塞車，請稍等一兩分鐘後再試一次喔！"
                try:
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )
                except Exception as reply_exc:
                    logger.error(f"Failed to send server error fallback reply: {reply_exc}", exc_info=True)

            except GeminiRateLimitError as exc:
                logger.warning(f"Gemini rate limit exceeded: {exc}")
                fallback_text = str(exc) if str(exc) else "目前查詢人數較多，請稍後再試！"
                try:
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )
                except Exception as reply_exc:
                    logger.error(f"Failed to send rate limit fallback reply: {reply_exc}", exc_info=True)

            except (GeminiAPIError, Exception) as exc:
                logger.error(f"Error executing price comparison pipeline: {exc}", exc_info=True)
                fallback_text = "系統處理時發生異常，請確認輸入內容或稍後再試。"
                try:
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )
                except Exception as reply_exc:
                    logger.error(f"Failed to send fallback reply: {reply_exc}", exc_info=True)


@app.post("/api/webhook", summary="LINE Messaging API Webhook Endpoint")
@app.post("/webhook", summary="LINE Messaging API Webhook Endpoint (Alias)")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(None, alias="X-Line-Signature"),
) -> Response:
    """
    LINE Webhook receiver endpoint.
    - Validates signature (`X-Line-Signature`).
    - Parses webhook events (Text and Image messages).
    - Runs full end-to-end price comparison pipeline.
    - Returns HTTP 200 on success, or HTTP 400 on signature / payload errors.
    """
    if not x_line_signature:
        logger.error("Missing X-Line-Signature header in request.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Line-Signature header",
        )

    channel_secret = settings.line_channel_secret or os.getenv("LINE_CHANNEL_SECRET", "")
    if not channel_secret:
        logger.error("LINE_CHANNEL_SECRET is not set in environment or config.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: LINE_CHANNEL_SECRET missing",
        )

    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
    except Exception as exc:
        logger.error(f"Failed to read request body: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request body encoding",
        )

    parser = WebhookParser(channel_secret)

    try:
        events = parser.parse(body_str, x_line_signature)
    except InvalidSignatureError:
        logger.warning("LINE signature verification failed.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )
    except Exception as exc:
        logger.error(f"Error parsing LINE webhook events: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not parse webhook payload",
        )

    try:
        access_token = settings.line_channel_access_token or os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
        await handle_line_events(events, access_token)
    except Exception as exc:
        logger.error(f"Unexpected error handling events: {exc}", exc_info=True)
        return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)

    return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)


# Mangum handler for AWS Lambda / Netlify Serverless Functions
handler = Mangum(app, lifespan="off")


async def fetch_and_generate_flex_message(
    keyword_ja: str,
    keyword_zh: Optional[str] = None,
    platform: str = "mercari",
    timeout_seconds: float = 2.0,
    min_valid_jpy: float = 300.0,
    search_url: Optional[str] = None,
    affiliate_id: Optional[str] = None,
    enable_dynamic_buttons: bool = True,
) -> Dict[str, Any]:
    """
    Lightweight helper to fetch min price for target platform (Mercari or Rakuten)
    and pass calculated min_price to Flex Message builder, replacing '(點擊查看)' fallback
    with '(約 NT${min_price})' or keeping '(點擊查看)' on failure/timeout.
    """
    price_twd = await fetch_lightweight_platform_min_price(
        platform=platform,
        query=keyword_ja,
        timeout_seconds=timeout_seconds,
        min_valid_jpy=min_valid_jpy,
    )
    clean_ja = normalize_search_keyword(keyword_ja) or "商品搜尋"
    target_url = search_url or construct_platform_search_url(platform, clean_ja)

    plat_lower = platform.lower().strip()
    return build_keyword_flex_message(
        japanese_keyword=clean_ja,
        search_url=target_url,
        affiliate_id=affiliate_id or settings.buyee_affiliate_id,
        affiliate_base_url=settings.affiliate_base_url,
        keyword_zh=keyword_zh or clean_ja,
        mercari_min_price=price_twd if plat_lower in ("mercari", "mercari_jp", "buyee") else None,
        rakuten_min_price=price_twd if plat_lower in ("rakuten", "rakuten_jp", "buyee_rakuten") else None,
        enable_dynamic_buttons=enable_dynamic_buttons,
    )

