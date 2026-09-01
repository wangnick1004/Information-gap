import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app, settings

client = TestClient(app)


def generate_signature(secret: str, body: str) -> str:
    """Compute HMAC-SHA256 signature for LINE webhook payload."""
    hash_value = hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(hash_value).decode("utf-8")


def test_health_check():
    """Test health check endpoints."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "line-price-comparison-bot"

    api_response = client.get("/api/health")
    assert api_response.status_code == 200
    assert api_response.json()["status"] == "healthy"


def test_webhook_missing_signature():
    """Test that requests missing X-Line-Signature return 400."""
    response = client.post("/api/webhook", json={"events": []})
    assert response.status_code == 400
    assert "Missing X-Line-Signature" in response.json()["detail"]


def test_webhook_invalid_signature():
    """Test that requests with an invalid signature return 400."""
    with patch.object(settings, "line_channel_secret", "test_channel_secret"):
        response = client.post(
            "/api/webhook",
            content=json.dumps({"events": []}),
            headers={"X-Line-Signature": "invalid_signature_string"},
        )
        assert response.status_code == 400
        assert "Invalid signature" in response.json()["detail"]


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_valid_text_message(mock_messaging_api_class, mock_api_client_class):
    """Test valid LINE text message webhook and echo reply."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    secret = "test_secret_123"
    token = "test_token_456"

    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100001",
                    "text": "https://www.facebook.com/groups/123/posts/456",
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
    body_str = json.dumps(payload)
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
        assert response.text == "OK"


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_rich_menu_guide_command(mock_messaging_api_class, mock_api_client_class):
    """Test '新手指南' interceptor returns guide text and bypasses search."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    secret = "test_secret_123"
    token = "test_token_456"

    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100002",
                    "text": "新手指南",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "token_guide_123",
                "mode": "active",
                "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZR",
                "deliveryContext": {"isRedelivery": False},
            }
        ],
    }
    body_str = json.dumps(payload)
    signature = generate_signature(secret, body_str)

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
        )
        assert response.status_code == 200

    mock_api.reply_message.assert_called_once()
    req = mock_api.reply_message.call_args[0][0]
    assert len(req.messages) == 1
    assert "【新手指南】" in req.messages[0].text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_rich_menu_disclaimer_command(mock_messaging_api_class, mock_api_client_class):
    """Test '平台比較與免責' interceptor returns disclaimer text and bypasses search."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    secret = "test_secret_123"
    token = "test_token_456"

    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100003",
                    "text": "平台比較與免責",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "token_disclaimer_123",
                "mode": "active",
                "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZR",
                "deliveryContext": {"isRedelivery": False},
            }
        ],
    }
    body_str = json.dumps(payload)
    signature = generate_signature(secret, body_str)

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
        )
        assert response.status_code == 200

    mock_api.reply_message.assert_called_once()
    req = mock_api.reply_message.call_args[0][0]
    assert len(req.messages) == 1
    assert "【平台與免責聲明】" in req.messages[0].text


@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_direct_keyword_search(mock_messaging_api_class, mock_api_client_class, mock_parse, mock_scrape):
    """Test standard keyword search (such as menu button sending 'Switch 2') directly invokes search pipeline."""
    from services.parser import ParsedItem
    from services.scraper import ScrapingResult

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse.return_value = ParsedItem(
        franchise="任天堂",
        character="Switch 2",
        item_type="主機",
        keyword_jp="Switch 2",
        keyword_zh="Switch 2",
        search_query_ja="Switch 2",
        fb_price_twd=12000,
        is_anime_merch=True,
    )
    mock_scrape.return_value = ScrapingResult(
        query="Switch 2",
        search_url="https://buyee.jp/mercari/search?keyword=Switch2",
        lowest_price_jpy=40000.0,
        median_price_jpy=45000.0,
        representative_image_url="https://example.com/switch2.jpg",
        sample_prices=[40000.0, 45000.0, 50000.0],
        total_found=3,
    )

    secret = "test_secret_123"
    token = "test_token_456"

    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100004",
                    "text": "Switch 2",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "token_demo_123",
                "mode": "active",
                "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZR",
                "deliveryContext": {"isRedelivery": False},
            }
        ],
    }
    body_str = json.dumps(payload)
    signature = generate_signature(secret, body_str)

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
        )
        assert response.status_code == 200

    mock_api.reply_message.assert_called_once()
    req = mock_api.reply_message.call_args[0][0]
    # Expect 1 message: ONLY the resulting FlexMessage
    assert len(req.messages) == 1
    assert req.messages[0].type == "flex"


def test_mangum_handler():
    """Test that the Mangum handler processes AWS Lambda / Netlify API Gateway events."""
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    from main import handler

    lambda_event = {
        "resource": "/",
        "path": "/",
        "httpMethod": "GET",
        "headers": {},
        "multiValueHeaders": {},
        "queryStringParameters": None,
        "multiValueQueryStringParameters": None,
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourcePath": "/",
            "httpMethod": "GET",
            "path": "/",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    response = handler(lambda_event, {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "healthy"
