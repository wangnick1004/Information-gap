import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

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
    assert "【創立初衷：打破資訊落差】" in req.messages[0].text
    assert "【使用免責聲明】" in req.messages[0].text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_rich_menu_shipping_guide_command(mock_messaging_api_class, mock_api_client_class):
    """Test '集運倉介紹' interceptor returns shipping guide text and bypasses search."""
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
                    "id": "100005",
                    "text": "集運倉介紹",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "token_shipping_123",
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
    assert "【集貨倉是什麼？】" in req.messages[0].text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_rich_menu_feedback_command(mock_messaging_api_class, mock_api_client_class):
    """Test '客服與回報' interceptor returns feedback form & contact text and bypasses search."""
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
                    "id": "100006",
                    "text": "客服與回報",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "token_feedback_123",
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
    assert "【客服與問題回報】" in req.messages[0].text
    assert "forms.gle" in req.messages[0].text
    assert "weiwei33442@gmail.com" in req.messages[0].text


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


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_follow_event(mock_messaging_api_class, mock_api_client_class):
    """Test FollowEvent triggers the welcome guide TextMessage."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    secret = "test_secret_123"
    token = "test_token_456"

    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "follow",
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser_new_friend"},
                "replyToken": "token_follow_123",
                "mode": "active",
                "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZR",
                "deliveryContext": {"isRedelivery": False},
                "follow": {"isUnblocked": False},
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
    assert "歡迎加入！【拒絕當韭菜，消弭資訊落差】" in req.messages[0].text
    assert "三大核心功能" in req.messages[0].text


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


def test_rate_limiter_logic():
    """Test unit rate limiter threshold and sliding window."""
    from main import is_rate_limited, user_request_timestamps

    test_uid = "U_test_rate_limit_unit"
    user_request_timestamps.pop(test_uid, None)

    # 1 to 5 requests should all be allowed
    for _ in range(5):
        assert is_rate_limited(test_uid) is False

    # 6th request within 60s should be rate limited
    assert is_rate_limited(test_uid) is True

    # After simulated 61 seconds, rate limit window resets
    with patch("time.time", return_value=user_request_timestamps[test_uid][-1] + 61.0):
        assert is_rate_limited(test_uid) is False


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_rate_limit_interception(mock_messaging_api_class, mock_api_client_class):
    """Test that users exceeding 5 searches/60s receive cooldown message and bypass search execution."""
    from main import user_request_timestamps

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api
    mock_api_client = MagicMock()
    mock_api_client_class.return_value.__aenter__.return_value = mock_api_client

    secret = "test_secret_123"
    token = "test_token_456"
    test_user = "U_spam_user_999"
    user_request_timestamps.pop(test_user, None)

    def make_payload(text_msg: str):
        return {
            "destination": "U1234567890",
            "events": [
                {
                    "type": "message",
                    "message": {
                        "type": "text",
                        "id": "100001",
                        "text": text_msg,
                        "quoteToken": "quote123",
                    },
                    "timestamp": 1625641600000,
                    "source": {"type": "user", "userId": test_user},
                    "replyToken": "nHuyWiB7yP5Zw52FIkcQobQuGDXCTA",
                    "mode": "active",
                    "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZR",
                    "deliveryContext": {"isRedelivery": False},
                }
            ],
        }

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token), \
         patch("main.parse_fb_post", new_callable=AsyncMock) as mock_parser, \
         patch("main.scrape_buyee_prices", new_callable=AsyncMock) as mock_scraper:

        # 1. First 5 search requests succeed
        for i in range(5):
            body_str = json.dumps(make_payload(f"搜尋商品_{i}"))
            sig = generate_signature(secret, body_str)
            res = client.post(
                "/api/webhook",
                content=body_str,
                headers={"Content-Type": "application/json", "X-Line-Signature": sig},
            )
            assert res.status_code == 200

        # 2. 6th search request triggers rate limit interception
        body_str_6th = json.dumps(make_payload("第6次搜尋"))
        sig_6th = generate_signature(secret, body_str_6th)
        res_6th = client.post(
            "/api/webhook",
            content=body_str_6th,
            headers={"Content-Type": "application/json", "X-Line-Signature": sig_6th},
        )
        assert res_6th.status_code == 200

        # Verify rate limit message was sent
        last_call_args = mock_api.reply_message.call_args[0][0]
        assert "⚠️ 系統冷卻中！您的搜尋頻率過高" in last_call_args.messages[0].text

        # 3. Rich menu commands (e.g. 新手指南) are NOT throttled even when rate limited
        body_str_guide = json.dumps(make_payload("新手指南"))
        sig_guide = generate_signature(secret, body_str_guide)
        res_guide = client.post(
            "/api/webhook",
            content=body_str_guide,
            headers={"Content-Type": "application/json", "X-Line-Signature": sig_guide},
        )
        assert res_guide.status_code == 200
        guide_call_args = mock_api.reply_message.call_args[0][0]
        assert "📖 【新手指南】" in guide_call_args.messages[0].text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_webhook_string_normalization_and_silent_autocorrect(
    mock_parse_fb_post, mock_scrape_buyee_prices, mock_messaging_api_class, mock_api_client_class
):
    """Test that incoming message is normalized with .strip().lower(), LLM produces perfected_keyword, and search is executed silently with UX feedback indicator."""
    from linebot.v3.messaging import FlexMessage
    from services.parser import ParsedItem
    from services.scraper import ScrapingResult

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    # LLM autocorrects generic query 'switch' to perfected_keyword 'Nintendo Switch'
    mock_parse_fb_post.return_value = ParsedItem(
        franchise="任天堂",
        character="Switch",
        item_type="遊戲主機",
        keyword_jp="Nintendo Switch",
        keyword_zh="Nintendo Switch",
        search_query_ja="Nintendo Switch",
        perfected_keyword="Nintendo Switch",
        fb_price_twd=8500,
        is_anime_merch=True,
    )
    mock_scrape_buyee_prices.return_value = ScrapingResult(
        query="Nintendo Switch",
        search_url="https://buyee.jp/mercari/search?keyword=NintendoSwitch",
        lowest_price_jpy=25000.0,
        median_price_jpy=28000.0,
        representative_image_url="https://example.com/switch.jpg",
        sample_prices=[25000.0, 28000.0, 30000.0],
        total_found=3,
    )

    secret = "test_secret_123"
    token = "test_token_456"

    # Input has leading/trailing spaces and mixed uppercase characters
    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100010",
                    "text": "   sWiTcH   ",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "U_autocorrect_user"},
                "replyToken": "token_ac_123",
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

    # 1. Verify that parse_fb_post received the normalized string 'switch' (.strip().lower())
    mock_parse_fb_post.assert_awaited_once()
    called_post_text = mock_parse_fb_post.call_args[1].get("post_text") or mock_parse_fb_post.call_args[0][0]
    assert called_post_text == "switch"

    # 2. Verify silent execution: scraper was directly called with perfected_keyword and top 15 items
    mock_scrape_buyee_prices.assert_awaited_once_with("Nintendo Switch", max_items=15)

    # 3. Verify reply_message sends a FlexMessage (no Quick Reply interception)
    mock_api.reply_message.assert_awaited_once()
    reply_req = mock_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1

    msg = reply_req.messages[0]
    assert isinstance(msg, FlexMessage)

    # 4. Verify UX feedback indicator and price range are present in the header
    card1 = msg.contents.contents[0]
    header_contents = card1.header.contents
    assert len(header_contents) >= 2
    assert header_contents[1].text == "🔎 已自動為您精準鎖定：Nintendo Switch"
    assert any("💰 跨國均價區間：" in (c.text or "") for c in header_contents)

    # 5. Verify platform buttons have injected minimum prices and fallbacks
    c1_btns = [c for c in card1.footer.contents if getattr(c, "type", None) == "button"]
    assert "Mercari (約 NT$" in c1_btns[0].action.label
    assert c1_btns[1].action.label == "日本雅虎 (點擊查看)"


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_webhook_silent_autocorrect_even_on_zero_results(
    mock_parse_fb_post, mock_scrape_buyee_prices, mock_messaging_api_class, mock_api_client_class
):
    """Test that when search yields zero results, it still silently returns Flex Message with UX indicator instead of Quick Reply."""
    from linebot.v3.messaging import FlexMessage
    from services.parser import ParsedItem
    from services.scraper import ScrapingResult

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse_fb_post.return_value = ParsedItem(
        franchise="Sony",
        character="WH-1000XM5",
        item_type="ヘッドホン",
        keyword_jp="Sony WH-1000XM5",
        keyword_zh="Sony WH-1000XM5",
        search_query_ja="Sony WH-1000XM5",
        perfected_keyword="Sony WH-1000XM5",
        fb_price_twd=None,
        is_anime_merch=True,
    )

    # Scraper returns zero results
    mock_scrape_buyee_prices.return_value = ScrapingResult(
        query="Sony WH-1000XM5",
        search_url="https://buyee.jp/mercari/search?keyword=test",
        lowest_price_jpy=0.0,
        median_price_jpy=0.0,
        representative_image_url=None,
        sample_prices=[],
        total_found=0,
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
                    "id": "100011",
                    "text": "sony wh-1000xm5",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "U_zero_res_user"},
                "replyToken": "token_zero_123",
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

    mock_api.reply_message.assert_awaited_once()
    reply_req = mock_api.reply_message.call_args[0][0]
    msg = reply_req.messages[0]
    assert isinstance(msg, FlexMessage)
    card1 = msg.contents.contents[0]
    header_contents = card1.header.contents
    assert header_contents[1].text == "🔎 已自動為您精準鎖定：Sony WH-1000XM5"
    c1_btns = [c for c in card1.footer.contents if getattr(c, "type", None) == "button"]
    assert c1_btns[0].action.label in ("Mercari (點擊查看)", "Mercari (約 NT$2969起)") or c1_btns[0].action.label.startswith("Mercari (約 NT$")


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_webhook_normal_flex_when_no_suggestion(
    mock_parse_fb_post, mock_scrape_buyee_prices, mock_messaging_api_class, mock_api_client_class
):
    """Test that when input is accurate and specific (no suggested_term), normal Flex Message is returned."""
    from linebot.v3.messaging import FlexMessage
    from services.parser import ParsedItem
    from services.scraper import ScrapingResult

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    # Specific input has suggested_term=None
    mock_parse_fb_post.return_value = ParsedItem(
        franchise="Apple",
        character="iPhone 15",
        item_type="智慧型手機",
        keyword_jp="Apple iPhone 15",
        keyword_zh="Apple iPhone 15",
        search_query_ja="Apple iPhone 15",
        suggested_term=None,
        fb_price_twd=25000,
        is_anime_merch=True,
    )

    mock_scrape_buyee_prices.return_value = ScrapingResult(
        query="Apple iPhone 15",
        search_url="https://buyee.jp/mercari/search?keyword=iphone15",
        lowest_price_jpy=95000.0,
        median_price_jpy=100000.0,
        representative_image_url="https://example.com/iphone15.jpg",
        sample_prices=[95000.0, 100000.0, 105000.0],
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
                    "id": "100012",
                    "text": "Apple iPhone 15",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "U_normal_user"},
                "replyToken": "token_normal_123",
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

    mock_api.reply_message.assert_awaited_once()
    reply_req = mock_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1
    assert isinstance(reply_req.messages[0], FlexMessage)


@patch("main.fetch_shopee_api_price", new_callable=AsyncMock)
@patch("main.fetch_mercari_api_price", new_callable=AsyncMock)
@patch("main.fetch_rakuten_min_price", new_callable=AsyncMock)
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_concurrent_shopee_dispatch_and_ui_injection(
    mock_messaging_api_class,
    mock_api_client_class,
    mock_parse,
    mock_scrape,
    mock_rakuten,
    mock_mercari,
    mock_shopee,
):
    """
    Test that fetch_shopee_api_price is concurrently dispatched alongside Mercari and Rakuten,
    and the returned Shopee price is correctly injected into the Flex Message UI button.
    """
    from services.parser import ParsedItem
    from services.scraper import ScrapingResult
    from linebot.v3.messaging import FlexMessage

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse.return_value = ParsedItem(
        franchise="任天堂",
        character="Switch OLED",
        item_type="主機",
        keyword_jp="Nintendo Switch",
        keyword_zh="Switch OLED",
        search_query_ja="Nintendo Switch",
        perfected_keyword="Nintendo Switch OLED",
        fb_price_twd=8500,
        is_anime_merch=True,
    )

    mock_scrape.return_value = ScrapingResult(
        query="Nintendo Switch",
        search_url="https://buyee.jp/mercari/search?keyword=Switch",
        lowest_price_jpy=30000.0,
        median_price_jpy=35000.0,
        representative_image_url="https://example.com/switch.jpg",
        sample_prices=[30000.0, 35000.0, 40000.0],
        total_found=3,
    )

    mock_rakuten.return_value = 7800
    mock_mercari.return_value = 7200
    mock_shopee.return_value = 6990

    secret = "test_secret_shopee"
    token = "test_token_shopee"

    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "text",
                    "id": "100099",
                    "text": "Switch OLED",
                    "quoteToken": "quote123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "U_shopee_user"},
                "replyToken": "token_shopee_123",
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

    # 1. Verify fetch_shopee_api_price was called with effective keyword
    mock_shopee.assert_awaited_once()
    called_kw = mock_shopee.call_args[0][0]
    assert "Switch" in called_kw

    # 2. Verify Flex Message contains injected Shopee price button
    mock_api.reply_message.assert_awaited_once()
    reply_req = mock_api.reply_message.call_args[0][0]
    flex_msg = reply_req.messages[0]
    assert isinstance(flex_msg, FlexMessage)
    flex_dict = flex_msg.contents.to_dict()

    def _find_buttons(node):
        btns = []
        if isinstance(node, dict):
            if node.get("type") == "button":
                btns.append(node)
            for v in node.values():
                btns.extend(_find_buttons(v))
        elif isinstance(node, list):
            for item in node:
                btns.extend(_find_buttons(item))
        return btns

    all_buttons = _find_buttons(flex_dict)
    shopee_btns = [
        b for b in all_buttons
        if "蝦皮" in (b.get("text") or b.get("action", {}).get("label", ""))
        or "shopee" in (b.get("text") or b.get("action", {}).get("label", "")).lower()
    ]
    assert len(shopee_btns) > 0
    btn_label = shopee_btns[0].get("text") or shopee_btns[0].get("action", {}).get("label", "")
    assert "6990" in btn_label


