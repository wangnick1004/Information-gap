import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app, settings
from services.parser import ParsedItem
from services.platforms import FetchStatus
from tests.fakes import FakeAdapter, FakeParser, failed, fake_platforms, found, generate_signature, pipeline_with

client = TestClient(app)


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


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_direct_keyword_search(mock_messaging_api_class, mock_api_client_class):
    """Test standard keyword search (such as menu button sending 'Switch 2') directly invokes search pipeline."""
    from services.parser import ParsedItem

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    parser = FakeParser(ParsedItem(
        franchise="任天堂",
        character="Switch 2",
        item_type="主機",
        keyword_jp="Switch 2",
        keyword_zh="Switch 2",
        search_query_ja="Switch 2",
        fb_price_twd=12000,
        is_anime_merch=True,
    ))
    mercari = found(40000.0, 45000.0, 50000.0, thumbnail='https://example.com/switch2.jpg')

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

    with patch("main.compare_prices", pipeline_with(parser, fake_platforms(mercari=mercari))), \
         patch.object(settings, "line_channel_secret", secret), \
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
                    "webhookEventId": f"ev_rate_limit_{text_msg}",
                    "deliveryContext": {"isRedelivery": False},
                }
            ],
        }

    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token), \
         patch("main.compare_prices", pipeline_with(FakeParser(ParsedItem(keyword_zh="商品")))):

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
def test_webhook_string_normalization_and_silent_autocorrect(
    mock_messaging_api_class, mock_api_client_class
):
    """Test that incoming message is normalized with .strip().lower(), LLM produces perfected_keyword, and search is executed silently with UX feedback indicator."""
    from linebot.v3.messaging import FlexMessage
    from services.parser import ParsedItem

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    # LLM autocorrects generic query 'switch' to perfected_keyword 'Nintendo Switch'
    parser = FakeParser(ParsedItem(
        franchise="任天堂",
        character="Switch",
        item_type="遊戲主機",
        keyword_jp="Nintendo Switch",
        keyword_zh="Nintendo Switch",
        search_query_ja="Nintendo Switch",
        perfected_keyword="Nintendo Switch",
        fb_price_twd=8500,
        is_anime_merch=True,
        # 含 Mercari 與日本雅虎的類別，才會出現 Mercari 完整比價卡
        category="動漫周邊/玩具",
    ))
    mercari = FakeAdapter(found(25000.0, 28000.0, 30000.0, thumbnail='https://example.com/switch.jpg'))

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

    with patch("main.compare_prices", pipeline_with(parser, fake_platforms(mercari=mercari))), \
         patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
        )
        assert response.status_code == 200

    # 1. Verify that parse_fb_post received the normalized string 'switch' (.strip().lower())
    assert len(parser.calls) == 1
    called_post_text = parser.calls[0]["post_text"]
    assert called_post_text == "switch"

    # 2. Verify silent execution: Mercari was searched directly with the AI keyword
    assert mercari.calls == ["Nintendo Switch"]

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
def test_webhook_silent_autocorrect_even_on_zero_results(
    mock_messaging_api_class, mock_api_client_class
):
    """Test that when search yields zero results, it still silently returns Flex Message with UX indicator instead of Quick Reply."""
    from linebot.v3.messaging import FlexMessage
    from services.parser import ParsedItem

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    parser = FakeParser(ParsedItem(
        franchise="Sony",
        character="WH-1000XM5",
        item_type="ヘッドホン",
        keyword_jp="Sony WH-1000XM5",
        keyword_zh="Sony WH-1000XM5",
        search_query_ja="Sony WH-1000XM5",
        perfected_keyword="Sony WH-1000XM5",
        fb_price_twd=None,
        is_anime_merch=True,
        category="3C 家電",
    ))

    # Scraper returns zero results
    mercari = failed(FetchStatus.NO_RESULTS)

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

    with patch("main.compare_prices", pipeline_with(parser, fake_platforms(mercari=mercari))), \
         patch.object(settings, "line_channel_secret", secret), \
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
    # 3C 家電不含 Mercari：日本卡只剩日本樂天（查無結果 → 點擊查看）
    assert [b.action.label for b in c1_btns] == ["日本樂天 (點擊查看)"]


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_normal_flex_when_no_suggestion(
    mock_messaging_api_class, mock_api_client_class
):
    """Test that when input is accurate and specific (no suggested_term), normal Flex Message is returned."""
    from linebot.v3.messaging import FlexMessage
    from services.parser import ParsedItem

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    # Specific input has suggested_term=None
    parser = FakeParser(ParsedItem(
        franchise="Apple",
        character="iPhone 15",
        item_type="智慧型手機",
        keyword_jp="Apple iPhone 15",
        keyword_zh="Apple iPhone 15",
        search_query_ja="Apple iPhone 15",
        suggested_term=None,
        fb_price_twd=25000,
        is_anime_merch=True,
    ))

    mercari = found(95000.0, 100000.0, 105000.0, thumbnail='https://example.com/iphone15.jpg')

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

    with patch("main.compare_prices", pipeline_with(parser, fake_platforms(mercari=mercari))), \
         patch.object(settings, "line_channel_secret", secret), \
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


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_concurrent_shopee_dispatch_and_ui_injection(
    mock_messaging_api_class,
    mock_api_client_class,
):
    """
    Test that fetch_shopee_api_price is concurrently dispatched alongside Mercari and Rakuten,
    and the returned Shopee price is correctly injected into the Flex Message UI button.
    """
    from services.parser import ParsedItem
    from linebot.v3.messaging import FlexMessage

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    parser = FakeParser(ParsedItem(
        franchise="任天堂",
        character="Switch OLED",
        item_type="主機",
        keyword_jp="Nintendo Switch",
        keyword_zh="Switch OLED",
        search_query_ja="Nintendo Switch",
        perfected_keyword="Nintendo Switch OLED",
        fb_price_twd=8500,
        is_anime_merch=True,
    ))

    mercari = found(30000.0, 35000.0, 40000.0, thumbnail='https://example.com/switch.jpg')

    shopee = FakeAdapter(found(6990.0, currency="TWD"))
    platforms = fake_platforms(mercari=mercari, rakuten=found(37000.0), shopee=shopee)

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

    with patch("main.compare_prices", pipeline_with(parser, platforms)), \
         patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token):

        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
        )
        assert response.status_code == 200

    # 1. Verify the Shopee adapter was queried with the effective keyword
    assert len(shopee.calls) == 1
    called_kw = shopee.calls[0]
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



def _collect_display_texts(node):
    """Collect every user-visible string (text / altText / button label) from a Flex dict."""
    texts = []
    if isinstance(node, dict):
        for key in ("text", "altText", "label"):
            if isinstance(node.get(key), str):
                texts.append(node[key])
        for v in node.values():
            texts.extend(_collect_display_texts(v))
    elif isinstance(node, list):
        for item in node:
            texts.extend(_collect_display_texts(item))
    return texts


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_webhook_all_platforms_fail_shows_no_price_numbers(
    mock_messaging_api_class,
    mock_api_client_class,
):
    """When every platform fails, the reply must offer links only — never any price number."""
    from services.parser import ParsedItem

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    parser = FakeParser(ParsedItem(
        franchise="",
        character="藍牙耳機",
        item_type="耳機",
        keyword_jp="ワイヤレスイヤホン",
        keyword_zh="藍牙耳機",
        search_query_ja="ワイヤレスイヤホン",
        perfected_keyword="藍牙耳機",
        fb_price_twd=None,
    ))
    platforms = fake_platforms(
        mercari=failed(FetchStatus.BLOCKED),
    )

    secret = "test_secret_allfail"
    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {"type": "text", "id": "100777", "text": "藍牙耳機", "quoteToken": "q"},
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "U_allfail_user"},
                "replyToken": "token_allfail",
                "mode": "active",
                "webhookEventId": "01FZ74A0TDDPYRVKNK77XKC3ZZ",
                "deliveryContext": {"isRedelivery": False},
            }
        ],
    }
    body_str = json.dumps(payload)

    with patch("main.compare_prices", pipeline_with(parser, platforms)), \
         patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", "test_token_allfail"):
        response = client.post(
            "/api/webhook",
            content=body_str,
            headers={"Content-Type": "application/json", "X-Line-Signature": generate_signature(secret, body_str)},
        )
        assert response.status_code == 200

    mock_api.reply_message.assert_awaited_once()
    reply_msg = mock_api.reply_message.call_args[0][0].messages[0]
    assert reply_msg.type == "flex"

    texts = _collect_display_texts(reply_msg.contents.to_dict())
    assert texts, "expected a card with visible text"
    for text in texts:
        assert not any(ch.isdigit() for ch in text), f"price-like number shown to user: {text!r}"
