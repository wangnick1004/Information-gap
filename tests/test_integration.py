import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from linebot.v3.messaging import FlexMessage, TextMessage

from main import app, settings
from services.parser import ParsedAnimeItem
from services.scraper import ScrapingError, ScrapingResult

client = TestClient(app)


def generate_signature(secret: str, body: str) -> str:
    """Compute HMAC-SHA256 signature for LINE webhook payload."""
    hash_value = hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(hash_value).decode("utf-8")


def create_line_text_payload(text: str) -> str:
    """Construct a mock LINE v3 webhook payload containing a single text message."""
    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100001",
                    "text": text,
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "nHuyWiB7yP5Zw52FIkcQobQuGDXCTA",
                "mode": "active",
                "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZR",
                "deliveryContext": {"isRedelivery": False},
            }
        ],
    }
    return json.dumps(payload)


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_end_to_end_pipeline_success(
    mock_parse_fb_post,
    mock_scrape_buyee_prices,
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test full end-to-end price comparison pipeline responding with a FlexMessage."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    # 1. Mock Parsed Item from Gemini
    mock_parse_fb_post.return_value = ParsedAnimeItem(
        franchise="ハイキュー!!",
        character="影山飛雄",
        item_type="もちもちマスコット",
        year_or_edition="2020",
        search_query_ja="ハイキュー 影山 もちもちマスコット 2020",
        fb_price_twd=1500,
        is_anime_merch=True,
    )

    # 2. Mock Scraped Results from Buyee
    mock_scrape_buyee_prices.return_value = ScrapingResult(
        query="ハイキュー 影山 もちもちマスコット 2020",
        search_url="https://buyee.jp/mercari/search?keyword=test",
        lowest_price_jpy=1200.0,
        median_price_jpy=1500.0,
        representative_image_url="https://static.mercdn.net/item/detail/orig/photos/m1.jpg",
        sample_prices=[1200.0, 1500.0, 1800.0],
        total_found=3,
    )

    secret = "secret_integration_test"
    token = "token_integration_test"
    post_text = "售 排少 影山 2020 趴娃 綁1 1500"
    body_str = create_line_text_payload(post_text)
    signature = generate_signature(secret, body_str)

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token), \
         patch.object(settings, "buyee_affiliate_id", "aff_tag_123"):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={
                "Content-Type": "application/json",
                "X-Line-Signature": signature,
            },
        )

        assert response.status_code == 200
        assert response.text == "OK"

        # Verify reply_message was called with FlexMessage
        mock_api.reply_message.assert_awaited_once()
        reply_request = mock_api.reply_message.call_args[0][0]
        assert reply_request.reply_token == "nHuyWiB7yP5Zw52FIkcQobQuGDXCTA"
        assert len(reply_request.messages) == 1

        sent_msg = reply_request.messages[0]
        assert isinstance(sent_msg, FlexMessage)
        assert "ハイキュー" in sent_msg.alt_text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_end_to_end_irrelevant_post_fallback(
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test that irrelevant user text triggers the friendly plain-text fallback message."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_text_payload("今天天氣真好")
    signature = generate_signature(secret, body_str)

    # Mock Gemini response indicating non-anime content
    mock_gemini_resp = MagicMock()
    mock_gemini_resp.text = json.dumps({
        "franchise": "",
        "character": "",
        "item_type": "",
        "search_query_ja": "",
        "fb_price_twd": None,
        "is_anime_merch": False,
    })

    with patch("services.parser.genai.Client") as mock_genai_client_class, \
         patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token), \
         patch.object(settings, "gemini_api_key", "dummy_key"):

        mock_genai_client = MagicMock()
        mock_genai_client_class.return_value = mock_genai_client
        mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_gemini_resp)

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={
                "Content-Type": "application/json",
                "X-Line-Signature": signature,
            },
        )

        assert response.status_code == 200
        mock_api.reply_message.assert_awaited_once()
        reply_request = mock_api.reply_message.call_args[0][0]
        sent_msg = reply_request.messages[0]
        assert isinstance(sent_msg, TextMessage)
        assert "無法解析此貼文" in sent_msg.text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_end_to_end_scraper_error_fallback(
    mock_parse_fb_post,
    mock_scrape_buyee_prices,
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test fallback message when parser succeeds but Japanese scraper finds no items."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse_fb_post.return_value = ParsedAnimeItem(
        franchise="鬼滅之刃",
        character="炭治郎",
        item_type="徽章",
        search_query_ja="鬼滅の刃 炭治郎 缶バッジ",
        fb_price_twd=300,
        is_anime_merch=True,
    )
    mock_scrape_buyee_prices.side_effect = ScrapingError("No listings found")

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_text_payload("售 鬼滅 炭治郎 徽章 300")
    signature = generate_signature(secret, body_str)

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={
                "Content-Type": "application/json",
                "X-Line-Signature": signature,
            },
        )

        assert response.status_code == 200
        mock_api.reply_message.assert_awaited_once()
        reply_request = mock_api.reply_message.call_args[0][0]
        sent_msg = reply_request.messages[0]
        assert isinstance(sent_msg, TextMessage)
        assert "未找到相符現貨" in sent_msg.text
