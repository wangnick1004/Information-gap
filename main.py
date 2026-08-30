import logging
import os
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
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import ImageMessageContent, MessageEvent, TextMessageContent
from mangum import Mangum
from pydantic import BaseModel
from config import Settings, settings
from services.flex_builder import (
    build_keyword_flex_message,
    build_price_comparison_flex,
)
# Alias FlexSendMessage for LINE SDK convention compatibility
FlexSendMessage = FlexMessage
from services.parser import (
    GeminiAPIError,
    GeminiRateLimitError,
    GeminiServerError,
    IrrelevantPostError,
    ParsedItem,
    parse_fb_post,
)
from services.pricing import PricingResult, calculate_landed_cost
from services.scraper import (
    ScrapingBlockedError,
    ScrapingError,
    ScrapingResult,
    ScrapingTimeoutError,
    scrape_buyee_prices,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("line_bot")

# FastAPI Application Initialization
app = FastAPI(
    title="LINE Price-Comparison Bot API",
    description="Stateless serverless backend for goods price comparison on LINE",
    version="1.0.0",
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    1. Extract text or fetch image bytes via LINE Blob API.
    2. Parse entities & generate Japanese search query with Gemini.
    3. Scrape real-time prices and thumbnail from Buyee Mercari.
    4. Calculate estimated landed cost in TWD and assess markup.
    5. Generate and reply with a LINE Flex Message bubble.
    6. Graceful fallback on errors to ensure user is always notified.
    """
    if not access_token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN is not configured; skipping API reply.")
        return

    configuration = Configuration(access_token=access_token)
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        line_bot_blob_api = AsyncMessagingApiBlob(api_client)

        for event in events:
            if not isinstance(event, MessageEvent):
                continue

            user_text: Optional[str] = None
            image_bytes: Optional[bytes] = None

            if isinstance(event.message, TextMessageContent):
                user_text = event.message.text.strip()
                logger.info(f"Processing text message from user: {user_text[:60]}...")
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

            # Step 0: Trigger LINE Loading Animation (typing indicator for user)
            user_id = getattr(event.source, "user_id", None)
            if user_id:
                try:
                    await line_bot_api.show_loading_animation(
                        ShowLoadingAnimationRequest(
                            chat_id=user_id,
                            loading_seconds=30,
                        )
                    )
                    logger.debug(f"Triggered LINE loading animation for user {user_id}")
                except Exception as anim_exc:
                    logger.debug(f"Failed to show loading animation (non-critical): {anim_exc}")

            parsed_item: Optional[ParsedItem] = None

            try:
                # Step 1: Multimodal Entity Extraction & Japanese Search Query Generation
                parsed_item = await parse_fb_post(post_text=user_text, image_data=image_bytes)

                # Step 2: Scrape Japanese Proxy Marketplace (Buyee Mercari)
                scraper_result = await scrape_buyee_prices(parsed_item.search_query_ja)

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

                # Step 4: Build LINE Flex Message UI with Affiliate Tracking
                flex_dict = build_price_comparison_flex(
                    parsed_item=parsed_item,
                    pricing_result=pricing_result,
                    scraper_result=scraper_result,
                    affiliate_id=settings.buyee_affiliate_id,
                    affiliate_base_url=settings.affiliate_base_url,
                    shopee_affiliate_base_url=settings.shopee_affiliate_base_url,
                    taobao_affiliate_base_url=settings.taobao_affiliate_base_url,
                )
                flex_container = FlexContainer.from_dict(flex_dict)
                alt_text = f"【比價分析】{parsed_item.franchise} {parsed_item.character}".strip()

                reply_msg = FlexMessage(alt_text=alt_text, contents=flex_container)
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[reply_msg],
                    )
                )
                logger.info("Successfully replied with Flex Message comparison card.")

            except IrrelevantPostError as exc:
                logger.info(f"Irrelevant post/image received: {exc}")
                fallback_text = "無法解析此貼文或圖片，請確認是否包含明確的商品名稱、型號或清晰外觀。"
                await line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=fallback_text)],
                    )
                )

            except (ScrapingTimeoutError, ScrapingBlockedError, ScrapingError) as exc:
                logger.warning(f"Scraping issue ({type(exc).__name__}): {exc}")
                if parsed_item and exc.search_url:
                    item_title = f"{parsed_item.franchise} {parsed_item.character}".strip()
                    keyword_flex_dict = build_keyword_flex_message(
                        japanese_keyword=parsed_item.keyword_jp or parsed_item.search_query_ja,
                        search_url=exc.search_url,
                        affiliate_id=settings.buyee_affiliate_id,
                        item_title=item_title,
                        affiliate_base_url=settings.affiliate_base_url,
                        keyword_zh=parsed_item.keyword_zh,
                        shopee_affiliate_base_url=settings.shopee_affiliate_base_url,
                        taobao_affiliate_base_url=settings.taobao_affiliate_base_url,
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
                else:
                    fallback_text = "已辨識商品，但在日本代購平台未找到相符現貨，請稍後再試。"
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )

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
