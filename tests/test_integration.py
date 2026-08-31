import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from linebot.v3.messaging import FlexMessage, TextMessage

from main import app, settings
from services.parser import ParsedItem
from services.scraper import ScrapingError, ScrapingResult, ScrapingTimeoutError

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


def create_line_image_payload(message_id: str = "img_msg_1001") -> str:
    """Construct a mock LINE v3 webhook payload containing a single image message."""
    payload = {
        "destination": "U1234567890",
        "events": [
            {
                "type": "message",
                "message": {
                    "type": "image",
                    "id": message_id,
                    "contentProvider": {"type": "line"},
                    "quoteToken": "quote_token_123",
                },
                "timestamp": 1625641600000,
                "source": {"type": "user", "userId": "Uuser123"},
                "replyToken": "reply_token_image_123",
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

    mock_parse_fb_post.return_value = ParsedItem(
        franchise="ハイキュー!!",
        character="影山飛雄",
        item_type="もちもちマスコット",
        year_or_edition="2020",
        search_query_ja="ハイキュー 影山 もちもちマスコット 2020",
        fb_price_twd=1500,
        is_anime_merch=True,
    )

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

        mock_api.reply_message.assert_awaited_once()
        reply_request = mock_api.reply_message.call_args[0][0]
        assert reply_request.reply_token == "nHuyWiB7yP5Zw52FIkcQobQuGDXCTA"
        assert len(reply_request.messages) == 1

        sent_msg = reply_request.messages[0]
        assert isinstance(sent_msg, FlexMessage)
        assert "ハイキュー" in sent_msg.alt_text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_end_to_end_pipeline_with_affiliate_base_url(
    mock_parse_fb_post,
    mock_scrape_buyee_prices,
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test webhook generates FlexMessage containing AFFILIATE_BASE_URL redirect action URI."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse_fb_post.return_value = ParsedItem(
        franchise="Sony",
        character="WH-1000XM5",
        item_type="ヘッドホン",
        search_query_ja="Sony WH-1000XM5 ヘッドホン",
        fb_price_twd=8000,
        is_anime_merch=True,
    )

    mock_scrape_buyee_prices.return_value = ScrapingResult(
        query="Sony WH-1000XM5 ヘッドホン",
        search_url="https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5",
        lowest_price_jpy=28000.0,
        median_price_jpy=32000.0,
        representative_image_url="https://static.mercdn.net/item/detail/orig/photos/sony.jpg",
        sample_prices=[28000.0, 32000.0],
        total_found=2,
    )

    secret = "secret_integration_test"
    token = "token_integration_test"
    post_text = "售 Sony WH-1000XM5 8000"
    body_str = create_line_text_payload(post_text)
    signature = generate_signature(secret, body_str)

    aff_base = "https://affiliate.example.com/redirect"
    shopee_aff_base = "https://affiliate.shopee.example.com/click"
    taobao_aff_base = "https://affiliate.taobao.example.com/click"
    yahoo_tw_aff_base = "https://affiliate.yahoo-tw.example.com/click"
    with patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token), \
         patch.object(settings, "buyee_affiliate_id", "aff_123"), \
         patch.object(settings, "affiliate_base_url", aff_base), \
         patch.object(settings, "shopee_affiliate_base_url", shopee_aff_base), \
         patch.object(settings, "taobao_affiliate_base_url", taobao_aff_base), \
         patch.object(settings, "yahoo_tw_affiliate_base_url", yahoo_tw_aff_base):

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
        assert isinstance(sent_msg, FlexMessage)

        flex_dict = sent_msg.contents.to_dict()
        assert flex_dict["type"] == "carousel"
        assert len(flex_dict["contents"]) == 2

        # Card 1 (Japan Focus)
        card_japan = flex_dict["contents"][0]
        japan_buttons = [c for c in card_japan["footer"]["contents"] if c.get("type") == "button"]
        assert len(japan_buttons) == 3

        # Buyee Mercari Button
        buyee_uri = japan_buttons[0]["action"]["uri"]
        assert buyee_uri.startswith("https://affiliate.example.com/redirect?t=")
        assert "https%3A%2F%2Fbuyee.jp%2Fmercari%2Fsearch" in buyee_uri
        assert "af%3Daff_123" in buyee_uri
        assert japan_buttons[0]["action"]["label"] == "前往 Mercari (直購)"

        # Buyee Yahoo Auctions Button
        yahoo_uri = japan_buttons[1]["action"]["uri"]
        assert yahoo_uri.startswith("https://affiliate.example.com/redirect?t=")
        assert "https%3A%2F%2Fbuyee.jp%2Fitem%2Fsearch%2Fquery" in yahoo_uri
        assert "af%3Daff_123" in yahoo_uri
        assert japan_buttons[1]["action"]["label"] == "前往 日本雅虎 (競標)"

        # Buyee Rakuten Button
        rakuten_uri = japan_buttons[2]["action"]["uri"]
        assert rakuten_uri.startswith("https://affiliate.example.com/redirect?t=")
        assert "https%3A%2F%2Fbuyee.jp%2Frakuten%2Fshopping%2Fsearch%2Fcategory%2F0%3Fquery%3D" in rakuten_uri
        assert "af%3Daff_123" in rakuten_uri
        assert japan_buttons[2]["action"]["label"] == "前往 日本樂天 (全新品)"

        # Card 2 (Greater China Focus)
        card_china = flex_dict["contents"][1]
        china_buttons = [c for c in card_china["footer"]["contents"] if c.get("type") == "button"]
        assert len(china_buttons) == 3

        # Shopee Button (with dynamic affiliate tracking redirect)
        shopee_uri = china_buttons[0]["action"]["uri"]
        assert shopee_uri.startswith("https://affiliate.shopee.example.com/click?t=")
        assert "https%3A%2F%2Fshopee.tw%2Fsearch%3Fkeyword%3D" in shopee_uri
        assert china_buttons[0]["action"]["label"] == "前往 台灣蝦皮"

        # Taobao Button (with dynamic affiliate tracking redirect)
        taobao_uri = china_buttons[1]["action"]["uri"]
        assert taobao_uri.startswith("https://affiliate.taobao.example.com/click?t=")
        assert "https%3A%2F%2Fworld.taobao.com%2Fsearch%2Fsearch.htm%3Fq%3D" in taobao_uri
        assert "%20" in taobao_uri
        assert "%2520" not in taobao_uri
        assert china_buttons[1]["action"]["label"] == "前往 淘寶"

        # Yahoo Taiwan Button (with dynamic affiliate tracking redirect)
        yahoo_tw_uri = china_buttons[2]["action"]["uri"]
        assert yahoo_tw_uri.startswith("https://affiliate.yahoo-tw.example.com/click?t=")
        assert "https%3A%2F%2Ftw.buy.yahoo.com%2Fsearch%2Fproduct%3Fp%3D" in yahoo_tw_uri
        assert china_buttons[2]["action"]["label"] == "前往 台灣 Yahoo"


@patch("main.AsyncMessagingApiBlob")
@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_end_to_end_image_message_success(
    mock_parse_fb_post,
    mock_scrape_buyee_prices,
    mock_messaging_api_class,
    mock_api_client_class,
    mock_blob_api_class,
):
    """Test full end-to-end price comparison pipeline for incoming image messages."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_blob_api = AsyncMock()
    mock_blob_api.get_message_content = AsyncMock(return_value=b"fake_image_bytes_123")
    mock_blob_api_class.return_value = mock_blob_api

    mock_parse_fb_post.return_value = ParsedItem(
        franchise="Sony",
        character="WH-1000XM5",
        item_type="ヘッドホン",
        search_query_ja="Sony WH-1000XM5 ヘッドホン",
        fb_price_twd=None,
        is_anime_merch=True,
    )

    mock_scrape_buyee_prices.return_value = ScrapingResult(
        query="Sony WH-1000XM5 ヘッドホン",
        search_url="https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5",
        lowest_price_jpy=28000.0,
        median_price_jpy=32000.0,
        representative_image_url="https://static.mercdn.net/item/detail/orig/photos/sony.jpg",
        sample_prices=[28000.0, 32000.0, 35000.0],
        total_found=3,
    )

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_image_payload("img_12345")
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
        mock_blob_api.get_message_content.assert_awaited_once_with("img_12345")
        mock_parse_fb_post.assert_awaited_once_with(post_text=None, image_data=b"fake_image_bytes_123")
        mock_api.show_loading_animation.assert_awaited_once()
        mock_api.reply_message.assert_awaited_once()


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
def test_end_to_end_vague_input_flex_fallback(
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test that vague user input triggers a Flex Message card with general category keywords instead of plain text."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_text_payload("求推薦底片相機")
    signature = generate_signature(secret, body_str)

    mock_gemini_resp = MagicMock()
    mock_gemini_resp.text = json.dumps({
        "franchise": "底片相機",
        "character": "底片相機",
        "item_type": "フィルムカメラ",
        "keyword_jp": "フィルムカメラ",
        "keyword_zh": "底片相機",
        "fb_price_twd": None,
        "is_anime_merch": True,
    })

    with patch("services.parser.genai.Client") as mock_genai_client_class, \
         patch.object(settings, "line_channel_secret", secret), \
         patch.object(settings, "line_channel_access_token", token), \
         patch.object(settings, "gemini_api_key", "dummy_key"):

        mock_genai_client = MagicMock()
        mock_genai_client_class.return_value = mock_genai_client
        mock_chat = MagicMock()
        mock_genai_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_gemini_resp)

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
        assert isinstance(sent_msg, FlexMessage)

        flex_dict = sent_msg.contents.to_dict()
        assert flex_dict["type"] == "carousel"
        assert len(flex_dict["contents"]) == 2
        card_japan_btns = [c for c in flex_dict["contents"][0]["footer"]["contents"] if c.get("type") == "button"]
        assert len(card_japan_btns) == 3
        card_china_btns = [c for c in flex_dict["contents"][1]["footer"]["contents"] if c.get("type") == "button"]
        assert len(card_china_btns) == 3





@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.scrape_buyee_prices")
@patch("main.parse_fb_post")
def test_end_to_end_scraper_timeout_fallback(
    mock_parse_fb_post,
    mock_scrape_buyee_prices,
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test fallback message when scraper encounters timeout."""
    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse_fb_post.return_value = ParsedItem(
        franchise="Sony",
        character="WH-1000XM5",
        item_type="ヘッドホン",
        search_query_ja="Sony WH-1000XM5 ヘッドホン",
        fb_price_twd=8000,
        is_anime_merch=True,
    )
    mock_scrape_buyee_prices.side_effect = ScrapingTimeoutError(
        "Timeout",
        search_url="https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5",
        query="Sony WH-1000XM5",
    )

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_text_payload("售 Sony WH-1000XM5 8000")
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
        assert isinstance(sent_msg, FlexMessage)
        assert "比價成功，來去撈便宜～" in sent_msg.alt_text


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

    mock_parse_fb_post.return_value = ParsedItem(
        franchise="鬼滅之刃",
        character="炭治郎",
        item_type="徽章",
        search_query_ja="鬼滅の刃 炭治郎 缶バッジ",
        fb_price_twd=300,
        is_anime_merch=True,
    )
    mock_scrape_buyee_prices.side_effect = ScrapingError(
        "No listings found",
        search_url="https://buyee.jp/mercari/search?keyword=test",
        query="test",
    )

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
        assert isinstance(sent_msg, FlexMessage)
        assert "比價成功，來去撈便宜～" in sent_msg.alt_text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.parse_fb_post")
def test_end_to_end_gemini_server_error_fallback(
    mock_parse_fb_post,
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test webhook graceful reply when Gemini API 503 ServerError persists after retries."""
    from services.parser import GeminiServerError

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse_fb_post.side_effect = GeminiServerError("目前 AI 伺服器大塞車，請稍等一兩分鐘後再試一次喔！")

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_text_payload("售 Sony WH-1000XM5 8000")
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
        assert "目前 AI 伺服器大塞車，請稍等一兩分鐘後再試一次喔！" in sent_msg.text


@patch("main.AsyncApiClient")
@patch("main.AsyncMessagingApi")
@patch("main.parse_fb_post")
def test_end_to_end_gemini_rate_limit_fallback(
    mock_parse_fb_post,
    mock_messaging_api_class,
    mock_api_client_class,
):
    """Test webhook graceful reply when Gemini API rate limit is exceeded after all retries."""
    from services.parser import GeminiRateLimitError

    mock_api = AsyncMock()
    mock_messaging_api_class.return_value = mock_api

    mock_parse_fb_post.side_effect = GeminiRateLimitError("目前查詢人數較多，請稍後再試！")

    secret = "secret_integration_test"
    token = "token_integration_test"
    body_str = create_line_text_payload("售 Sony WH-1000XM5 8000")
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
        assert "目前查詢人數較多，請稍後再試！" in sent_msg.text
