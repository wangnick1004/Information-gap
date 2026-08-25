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
