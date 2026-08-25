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
    Configuration,
    FlexContainer,
    FlexMessage,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from mangum import Mangum
from pydantic import BaseModel
from config import Settings, settings
from services.flex_builder import build_price_comparison_flex
from services.parser import (
    GeminiAPIError,
    IrrelevantPostError,
    ParsedAnimeItem,
    parse_fb_post,
)
from services.pricing import PricingResult, calculate_landed_cost
from services.scraper import ScrapingError, ScrapingResult, scrape_buyee_prices

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("line_bot")

# FastAPI Application Initialization
app = FastAPI(
    title="LINE Price-Comparison Bot API",
    description="Stateless serverless backend for anime goods price comparison on LINE",
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
    Process incoming LINE webhook events with the end-to-end price comparison pipeline:
    1. Parse FB text using Gemini 1.5 Flash structured output.
    2. Scrape real-time prices and thumbnail from Buyee Mercari.
    3. Calculate estimated landed cost in TWD and assess markup.
    4. Generate and reply with a LINE Flex Message bubble.
    5. Graceful fallback on errors to ensure user is always notified.
    """
    if not access_token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN is not configured; skipping API reply.")
        return

    configuration = Configuration(access_token=access_token)
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)

        for event in events:
            if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                user_text = event.message.text.strip()
                logger.info(f"Processing text message from user: {user_text[:60]}...")

                parsed_item: Optional[ParsedAnimeItem] = None

                try:
                    # Step 1: NLP Entity Extraction & Japanese Search Query Generation
                    parsed_item = await parse_fb_post(user_text)

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
                    logger.info(f"Irrelevant post received: {exc}")
                    fallback_text = "無法解析此貼文，請確認是否包含明確的動漫商品名稱與作品名。"
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )

                except ScrapingError as exc:
                    logger.warning(f"Scraping error: {exc}")
                    item_desc = ""
                    if parsed_item and (parsed_item.franchise or parsed_item.character):
                        item_desc = f"（{parsed_item.franchise} {parsed_item.character}）"
                    fallback_text = f"已辨識商品{item_desc}，但在日本代購平台未找到相符現貨或連線逾時，請稍後再試。"
                    await line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=fallback_text)],
                        )
                    )

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
    - Parses webhook events.
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
