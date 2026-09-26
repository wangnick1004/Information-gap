import asyncio
from collections import defaultdict
import logging
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response, status
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
from pydantic import BaseModel
from config import Settings, settings
from services.cache import search_cache
from services.cache import TTLCache
from services.clock import system_clock
from services.comparison import GEMINI_VISION_PROMPT, compare_prices, comparison_cache_key
from services.flex_builder import (
    build_comparison_flex,
    build_keyword_flex_message,
)
# Alias FlexSendMessage and TextSendMessage for LINE SDK convention compatibility
FlexSendMessage = FlexMessage
TextSendMessage = TextMessage
from services.parser import (
    AI_BUSY_MESSAGE,
    GeminiRateLimitError,
    GeminiServerError,
)
# Re-exported via app.py
from services.pricing import (
    DynamicPriceResult,
    calculate_dynamic_platform_prices,
    convert_to_twd,
    remove_outliers,
)
from services.scraper import normalize_search_keyword
from services.lightweight_fetcher import (
    construct_platform_search_url,
    fetch_lightweight_platform_min_price,
    fetch_lightweight_prices,
    fetch_mercari_min_price,
    fetch_rakuten_min_price,
    filter_extreme_low_prices,
    parse_platform_first_page_prices,
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

from contextlib import asynccontextmanager

# Predefined Hot Keywords for Background Cache Pre-warming
PREWARM_KEYWORDS = [
    "Switch 2",
    "PS5",
    "AirPods Pro",
    "Dyson 吹風機",
    "AJ1",
    "小棕瓶",
]


async def prewarm_search_cache() -> None:
    """
    Background pre-warming task that pre-fetches and caches search comparisons
    for popular hot keywords on app startup, ensuring instant (< 10ms) responses for users.
    """
    logger.info("🔥 [Cache Pre-warm] Starting background cache pre-warming for hot keywords...")
    for kw in PREWARM_KEYWORDS:
        try:
            # 只快取至少有一個平台查到價格的結果：啟動時抓價失敗不應讓使用者一小時內都拿到無價格的卡片
            result = await compare_prices(text=kw, cache=TTLCache())
            if not result.has_price:
                continue
            search_cache.set(comparison_cache_key(kw), result, ttl=3600.0)
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


GENERIC_ERROR_MESSAGE = "系統處理時發生異常，請確認輸入內容或稍後再試。"

# 已受理的 webhook event id（LINE 重送時 id 相同），保留一天
processed_webhook_event_ids = TTLCache(default_ttl=86400.0, max_size=10000)


def claim_new_events(events: list) -> list:
    """回傳尚未受理過的事件，並將其標記為已受理；重送的事件被略過。"""
    fresh = []
    for event in events:
        event_id = getattr(event, "webhook_event_id", None)
        if event_id:
            if event_id in processed_webhook_event_ids:
                logger.info(f"Skipping already-received webhook event {event_id}")
                continue
            processed_webhook_event_ids.set(event_id, True)
        fresh.append(event)
    return fresh


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


async def handle_line_events(events: list, access_token: str, received_at: Optional[float] = None) -> None:
    """
    背景處理 LINE webhook 事件（webhook 已先回應 200）。
    received_at 為收到 webhook 時的 system_clock.monotonic()，比價的 15 秒截止由此起算。

    每個事件各自並行處理：任何例外都會被捕捉、記錄並以降級訊息回覆，
    不影響同批其他事件，也不會讓服務崩潰。若 LINE 連線本身出錯，
    以新建立的連線對尚未回覆的事件補送降級訊息。
    """
    answered: set[int] = set()

    try:
        async with AsyncApiClient(Configuration(access_token=access_token)) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            line_bot_blob_api = AsyncMessagingApiBlob(api_client)

            async def handle_isolated(index: int, event: Any) -> None:
                try:
                    await handle_line_event(event, line_bot_api, line_bot_blob_api, received_at)
                except Exception as exc:
                    logger.error(f"Unhandled error while handling LINE event: {exc}", exc_info=True)
                    await reply_text_safely(line_bot_api, event, GENERIC_ERROR_MESSAGE)
                answered.add(index)

            # 同批事件並行處理，避免後面事件的 reply token 排隊到過期
            await asyncio.gather(*(handle_isolated(i, event) for i, event in enumerate(events)))
    except Exception as exc:
        logger.error(f"Unexpected error handling LINE events: {exc}", exc_info=True)
        unanswered = [event for i, event in enumerate(events) if i not in answered]
        if unanswered:
            await reply_error_with_fresh_client(unanswered, access_token)


async def reply_error_with_fresh_client(events: list, access_token: str) -> None:
    """原本的 LINE 連線故障時，另建連線對這些事件回覆降級訊息；再失敗只記錄。"""
    try:
        async with AsyncApiClient(Configuration(access_token=access_token)) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await asyncio.gather(
                *(reply_text_safely(line_bot_api, event, GENERIC_ERROR_MESSAGE) for event in events)
            )
    except Exception as exc:
        logger.error(f"Failed to send fallback replies with a fresh LINE client: {exc}", exc_info=True)


async def reply_text(line_bot_api: AsyncMessagingApi, event: Any, text: str) -> None:
    """以文字回覆事件；失敗時拋出，交由上層降級處理。"""
    await line_bot_api.reply_message(
        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=text)])
    )


async def reply_text_safely(line_bot_api: AsyncMessagingApi, event: Any, text: str) -> None:
    """盡力以文字回覆事件；失敗只記錄，不再拋出。"""
    if not getattr(event, "reply_token", None):
        return
    try:
        await reply_text(line_bot_api, event, text)
    except Exception as exc:
        logger.error(f"Failed to send fallback reply: {exc}", exc_info=True)


# Rich Menu 指令（含舊名稱）→ 回覆文字；這些指令不經比價、不受限流
RICH_MENU_RESPONSES: Dict[str, str] = {
    "新手指南": GUIDE_RESPONSE_TEXT,
    "新手圖解指南": GUIDE_RESPONSE_TEXT,
    "平台比較與免責": DISCLAIMER_RESPONSE_TEXT,
    "法律免責聲明": DISCLAIMER_RESPONSE_TEXT,
    "集運倉介紹": SHIPPING_GUIDE_RESPONSE_TEXT,
    "集貨倉介紹": SHIPPING_GUIDE_RESPONSE_TEXT,
    "客服與回報": FEEDBACK_RESPONSE_TEXT,
    "客服與問題回報": FEEDBACK_RESPONSE_TEXT,
}

IMAGE_DOWNLOAD_FAILED_MESSAGE = "無法下載您傳送的圖片，請稍後再試或直接提供文字描述。"


async def handle_line_event(
    event: Any,
    line_bot_api: AsyncMessagingApi,
    line_bot_blob_api: AsyncMessagingApiBlob,
    received_at: Optional[float] = None,
) -> None:
    """
    處理單一 LINE 事件：
    1. FollowEvent 回覆歡迎說明。
    2. Rich Menu 指令（新手指南、平台比較與免責、集運倉介紹、客服與回報）。
    3. 文字或圖片（經 LINE Blob API 下載）交給比價流程入口，結果轉成卡片回覆。
    4. AI 故障時回覆對應的降級訊息；其他例外往上拋，由 handle_line_events 回覆通用降級訊息。
    """
    if isinstance(event, FollowEvent):
        logger.info(f"Handling FollowEvent from user {getattr(event.source, 'user_id', 'unknown')}")
        await reply_text(line_bot_api, event, WELCOME_RESPONSE_TEXT)
        return

    if not isinstance(event, MessageEvent):
        return

    user_text: Optional[str] = None
    image_bytes: Optional[bytes] = None

    if isinstance(event.message, TextMessageContent):
        user_text = event.message.text.strip().lower()
        logger.info(f"Processing text message from user: {user_text[:60]}...")

        menu_response = RICH_MENU_RESPONSES.get(user_text)
        if menu_response is not None:
            logger.info(f"Handling '{user_text}' rich menu command.")
            await reply_text(line_bot_api, event, menu_response)
            return

    elif isinstance(event.message, ImageMessageContent):
        logger.info(f"Processing image message id={event.message.id} from user...")
        try:
            image_bytes = await line_bot_blob_api.get_message_content(event.message.id)
            logger.info(f"Successfully retrieved {len(image_bytes)} bytes of image content.")
        except Exception as exc:
            logger.error(f"Failed to retrieve image blob: {exc}", exc_info=True)
            await reply_text(line_bot_api, event, IMAGE_DOWNLOAD_FAILED_MESSAGE)
            return
    else:
        # Unsupported message type (stickers, audio, etc.)
        return

    user_id = getattr(event.source, "user_id", None)

    # Rate Limiting Intercept: Strictly allow max 5 searches per 60 seconds per user
    if user_id and is_rate_limited(user_id):
        logger.warning(
            f"🛑 [Rate Limit Exceeded] User {user_id} exceeded {RATE_LIMIT_MAX_REQUESTS} searches per {RATE_LIMIT_WINDOW_SECONDS}s."
        )
        await reply_text(line_bot_api, event, RATE_LIMIT_COOLDOWN_MESSAGE)
        return

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

    try:
        result = await compare_prices(text=user_text, image=image_bytes, received_at=received_at)
    except GeminiServerError as exc:
        logger.warning(f"Gemini server error (503 UNAVAILABLE): {exc}")
        await reply_text_safely(line_bot_api, event, str(exc) or AI_BUSY_MESSAGE)
        return
    except GeminiRateLimitError as exc:
        logger.warning(f"Gemini rate limit exceeded: {exc}")
        await reply_text_safely(line_bot_api, event, str(exc) or "目前查詢人數較多，請稍後再試！")
        return

    flex_dict, alt_text = build_comparison_flex(result)
    reply_msg = FlexMessage(alt_text=alt_text, contents=FlexContainer.from_dict(flex_dict))
    await line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[reply_msg],
        )
    )
    logger.info("Successfully replied with Flex Message comparison card.")


@app.post("/api/webhook", summary="LINE Messaging API Webhook Endpoint")
@app.post("/webhook", summary="LINE Messaging API Webhook Endpoint (Alias)")
async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: Optional[str] = Header(None, alias="X-Line-Signature"),
) -> Response:
    """
    LINE Webhook receiver endpoint.
    - Validates signature (`X-Line-Signature`).
    - Parses webhook events (Text and Image messages).
    - Skips events already received (LINE redelivery with the same webhook event id).
    - Responds immediately; events are handled in the background and replied via reply token.
    - Returns HTTP 200 on success, or HTTP 400 on signature / payload errors.
    """
    # 15 秒回覆截止自收到訊息起算
    received_at = system_clock.monotonic()
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

    # 先回應 LINE 200，比價於回應送出後在背景執行，避免 webhook 逾時與重送
    access_token = settings.line_channel_access_token or os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not access_token:
        # 不標記為已受理：修好設定後 LINE 重送的事件仍能被處理
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN is not configured; skipping API reply.")
        return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)
    background_tasks.add_task(handle_line_events, claim_new_events(events), access_token, received_at)

    return Response(content="OK", media_type="text/plain", status_code=status.HTTP_200_OK)


async def fetch_and_generate_flex_message(
    keyword_ja: str,
    keyword_zh: Optional[str] = None,
    platform: str = "mercari",
    timeout_seconds: float = 8.0,
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

