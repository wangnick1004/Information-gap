import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import pytest
from google.genai.errors import APIError, ServerError

from services.parser import (
    GeminiAPIError,
    GeminiRateLimitError,
    GeminiServerError,
    IrrelevantPostError,
    ParsedItem,
    compress_and_resize_image,
    parse_fb_post,
    resolve_model_name,
)


def test_compress_and_resize_image():
    """Test image resizing to 800px bounding box and JPEG compression."""
    # 1. Test with large RGBA image (transparency conversion to RGB)
    large_img = Image.new("RGBA", (2000, 1500), color=(255, 0, 0, 128))
    comp_bytes, mime = compress_and_resize_image(large_img, max_dimension=800, quality=85)
    assert mime == "image/jpeg"
    assert len(comp_bytes) > 0

    out_img = Image.open(io.BytesIO(comp_bytes))
    assert out_img.width == 800
    assert out_img.height == 600
    assert out_img.mode == "RGB"

    # 2. Test with raw bytes input (simulating LINE API download)
    raw_buf = io.BytesIO()
    Image.new("RGBA", (1600, 1200), color="blue").save(raw_buf, format="PNG")
    raw_bytes = raw_buf.getvalue()

    comp_bytes_from_raw, mime_from_raw = compress_and_resize_image(raw_bytes, max_dimension=800, quality=85)
    assert mime_from_raw == "image/jpeg"
    assert len(comp_bytes_from_raw) < len(raw_bytes)

    out_from_raw = Image.open(io.BytesIO(comp_bytes_from_raw))
    assert out_from_raw.width == 800
    assert out_from_raw.height == 600
    assert out_from_raw.mode == "RGB"


def test_resolve_model_name():
    """Test model name normalization and legacy alias redirection."""
    assert resolve_model_name("models/gemini-1.5-flash") == "gemini-flash-latest"
    assert resolve_model_name("gemini-1.5-flash") == "gemini-flash-latest"
    assert resolve_model_name("gemini-1.5-flash-latest") == "gemini-flash-latest"
    assert resolve_model_name("gemini-1.5-pro") == "gemini-flash-latest"
    assert resolve_model_name("gemini-pro") == "gemini-flash-latest"
    assert resolve_model_name("models/gemini-3.6-flash") == "gemini-3.6-flash"
    assert resolve_model_name("gemini-2.5-flash") == "gemini-2.5-flash"
    assert resolve_model_name("") == "gemini-flash-latest"
    assert resolve_model_name(None) == "gemini-flash-latest"


@pytest.mark.anyio
async def test_parse_fb_post_electronics_success():
    """Test successful entity extraction and translation from a consumer electronics post."""
    post_text = "售 Sony WH-1000XM5 耳罩式降噪耳機 黑色 95成新 降價求出 8500"

    expected_payload = {
        "franchise": "Sony",
        "character": "WH-1000XM5",
        "item_type": "ヘッドホン",
        "year_or_edition": None,
        "search_query_ja": "Sony WH-1000XM5 ヘッドホン",
        "fb_price_twd": 8500,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert result.character == "WH-1000XM5"
        assert result.item_type == "ヘッドホン"
        assert result.search_query_ja == "SONY WH-1000XM5 ヘッドホン"
        assert result.keyword_jp == "SONY WH-1000XM5 ヘッドホン"
        assert result.keyword_zh == "SONY WH-1000XM5"
        assert result.fb_price_twd == 8500
        assert result.is_anime_merch is True

        mock_client.aio.chats.create.assert_called_once()
        mock_chat.send_message.assert_awaited_once()


@pytest.mark.anyio
async def test_parse_fb_post_dual_keywords_success():
    """Test Gemini extraction with both keyword_jp (Buyee) and keyword_zh (Shopee/Taobao)."""
    post_text = "售 Sony WH-1000XM5 耳罩式降噪耳機 黑色 95成新 8500"

    expected_payload = {
        "franchise": "Sony",
        "character": "WH-1000XM5",
        "item_type": "ヘッドホン",
        "year_or_edition": None,
        "keyword_jp": "Sony WH-1000XM5 ヘッドホン",
        "keyword_zh": "Sony WH-1000XM5 耳機",
        "fb_price_twd": 8500,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.keyword_jp == "SONY WH-1000XM5 ヘッドホン"
        assert result.keyword_zh == "SONY WH-1000XM5 耳機"
        assert result.search_query_ja == "SONY WH-1000XM5 ヘッドホン"


@pytest.mark.anyio
async def test_parse_fb_post_image_only_success():
    """Test image-only multimodal recognition with raw bytes."""
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), color="green").save(buf, format="JPEG")
    fake_image_bytes = buf.getvalue()

    expected_payload = {
        "franchise": "Canon",
        "character": "EOS R6 Mark II",
        "item_type": "ミラーレス一眼カメラ",
        "year_or_edition": "Mark II",
        "search_query_ja": "Canon EOS R6 Mark II ミラーレス一眼",
        "fb_price_twd": None,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(image_data=fake_image_bytes, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Canon"
        assert result.character == "EOS R6 Mark II"
        assert result.search_query_ja == "CANON EOS R6 MARK II ミラーレス一眼"
        assert result.is_anime_merch is True

        mock_client.aio.chats.create.assert_called_once()
        mock_chat.send_message.assert_awaited_once()

        # Verify that DEFAULT_VISION_PROMPT is delivered to Gemini
        sent_message = mock_chat.send_message.call_args[1]["message"]
        from services.parser import DEFAULT_VISION_PROMPT
        assert isinstance(sent_message, list)
        assert DEFAULT_VISION_PROMPT in sent_message


@pytest.mark.anyio
async def test_parse_fb_post_custom_vision_prompt():
    """Test passing custom vision prompt to parse_fb_post."""
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color="red").save(buf, format="JPEG")
    fake_image_bytes = buf.getvalue()
    custom_prompt = "Custom strict vision prompt for identification"

    expected_payload = {
        "franchise": "Fujifilm",
        "character": "X100V",
        "item_type": "相機",
        "year_or_edition": "黑色",
        "search_query_ja": "Fujifilm X100V 黑色",
        "fb_price_twd": None,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(
            image_data=fake_image_bytes,
            vision_prompt=custom_prompt,
            api_key="fake_api_key",
        )

        assert isinstance(result, ParsedItem)
        mock_chat.send_message.assert_awaited_once()
        sent_message = mock_chat.send_message.call_args[1]["message"]
        assert custom_prompt in sent_message



@pytest.mark.anyio
async def test_parse_fb_post_pil_image_and_text():
    """Test multimodal recognition with PIL Image and accompanying user text."""
    img = Image.new("RGB", (50, 50), color="blue")
    post_text = "求這雙鞋日本行情"

    expected_payload = {
        "franchise": "Nike",
        "character": "Air Jordan 1 Retro High OG",
        "item_type": "スニーカー",
        "year_or_edition": "Chicago",
        "search_query_ja": "Nike Air Jordan 1 Chicago スニーカー",
        "fb_price_twd": None,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text=post_text, image_data=img, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Nike"
        assert result.character == "Air Jordan 1 Retro High OG"
        assert result.search_query_ja == "NIKE AIR JORDAN 1 CHICAGO スニーカー"

        mock_client.aio.chats.create.assert_called_once()
        mock_chat.send_message.assert_awaited_once()


@pytest.mark.anyio
async def test_parse_fb_post_anime_merch_success():
    """Test successful entity extraction and translation from a slang-heavy FB post."""
    post_text = "售 排少 影山 2020 趴娃 綁1 1500"

    expected_payload = {
        "franchise": "ハイキュー!!",
        "character": "影山飛雄",
        "item_type": "もちもちマスコット",
        "year_or_edition": "2020",
        "search_query_ja": "ハイキュー 影山飛雄 もちもちマスコット 2020",
        "fb_price_twd": 1500,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "ハイキュー!!"
        assert result.character == "影山飛雄"
        assert result.item_type == "もちもちマスコット"
        assert result.year_or_edition == "2020"
        assert result.search_query_ja == "ハイキュー 影山飛雄 もちもちマスコット 2020"
        assert result.fb_price_twd == 1500
        assert result.is_anime_merch is True

        mock_client.aio.chats.create.assert_called_once()
        mock_chat.send_message.assert_awaited_once()


@pytest.mark.anyio
async def test_parse_fb_post_404_model_fallback():
    """Test that if a model throws 404 NOT_FOUND, it automatically falls back to gemini-flash-latest."""
    post_text = "售 Sony WH-1000XM5 8000"

    expected_payload = {
        "franchise": "Sony",
        "character": "WH-1000XM5",
        "item_type": "ヘッドホン",
        "year_or_edition": None,
        "search_query_ja": "Sony WH-1000XM5 ヘッドホン",
        "fb_price_twd": 8000,
        "is_anime_merch": True,
    }
    mock_success_response = MagicMock()
    mock_success_response.text = json.dumps(expected_payload)

    error_404 = APIError(404, {"message": "models/gemini-custom-nonexistent is not found", "status": "NOT_FOUND"})

    with patch("services.parser.genai.Client") as mock_client_class, \
         patch("services.parser.os.getenv", return_value="gemini-custom-nonexistent"):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(
            side_effect=[error_404, mock_success_response]
        )

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert mock_chat.send_message.await_count == 2
        # Verify second call used gemini-flash-latest
        second_call_kwargs = mock_client.aio.chats.create.call_args_list[1].kwargs
        assert second_call_kwargs["model"] == "gemini-flash-latest"


@pytest.mark.anyio
async def test_parse_fb_post_503_server_error_retry_success():
    """Test that 503 UNAVAILABLE ServerError triggers exponential backoff retry and succeeds."""
    post_text = "售 Sony WH-1000XM5 8000"

    expected_payload = {
        "franchise": "Sony",
        "character": "WH-1000XM5",
        "item_type": "ヘッドホン",
        "year_or_edition": None,
        "search_query_ja": "Sony WH-1000XM5 ヘッドホン",
        "fb_price_twd": 8000,
        "is_anime_merch": True,
    }
    mock_success_response = MagicMock()
    mock_success_response.text = json.dumps(expected_payload)

    server_503_error = ServerError(503, {"message": "503 UNAVAILABLE: The model is overloaded. Please try again later.", "status": "UNAVAILABLE"})

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        # First call fails with 503 ServerError, second call succeeds
        mock_chat.send_message = AsyncMock(
            side_effect=[server_503_error, mock_success_response]
        )

        result = await parse_fb_post(post_text, api_key="fake_api_key", retry_delay_seconds=0.01)

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert mock_chat.send_message.await_count == 2


@pytest.mark.anyio
async def test_parse_fb_post_503_server_error_exceeded():
    """Test that persistent 503 UNAVAILABLE raises GeminiServerError with friendly user-facing message."""
    post_text = "售 Sony WH-1000XM5 8000"
    server_503_error = ServerError(503, {"message": "503 UNAVAILABLE: high demand", "status": "UNAVAILABLE"})

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(side_effect=server_503_error)

        with pytest.raises(GeminiServerError) as exc_info:
            await parse_fb_post(post_text, api_key="fake_api_key", max_retries=3, retry_delay_seconds=0.01)

        assert "目前 AI 伺服器大塞車，請稍等一兩分鐘後再試一次喔！" in str(exc_info.value)
        assert mock_chat.send_message.await_count == 3


@pytest.mark.anyio
async def test_parse_fb_post_rate_limit_retry_success():
    """Test that 429 rate limit triggers automatic retry and succeeds on subsequent attempt."""
    post_text = "售 Sony WH-1000XM5 8000"

    expected_payload = {
        "franchise": "Sony",
        "character": "WH-1000XM5",
        "item_type": "ヘッドホン",
        "year_or_edition": None,
        "search_query_ja": "Sony WH-1000XM5 ヘッドホン",
        "fb_price_twd": 8000,
        "is_anime_merch": True,
    }
    mock_success_response = MagicMock()
    mock_success_response.text = json.dumps(expected_payload)

    rate_limit_error = APIError(429, {"message": "RESOURCE_EXHAUSTED: quota exceeded", "status": "RESOURCE_EXHAUSTED"})

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        # First call fails with 429, second call succeeds
        mock_chat.send_message = AsyncMock(
            side_effect=[rate_limit_error, mock_success_response]
        )

        result = await parse_fb_post(post_text, api_key="fake_api_key", retry_delay_seconds=0.01)

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert mock_chat.send_message.await_count == 2


@pytest.mark.anyio
async def test_parse_fb_post_rate_limit_exceeded():
    """Test that persistent 429 rate limit raises GeminiRateLimitError after 3 retries."""
    post_text = "售 Sony WH-1000XM5 8000"
    rate_limit_error = APIError(429, {"message": "RESOURCE_EXHAUSTED", "status": "RESOURCE_EXHAUSTED"})

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(side_effect=rate_limit_error)

        with pytest.raises(GeminiRateLimitError) as exc_info:
            await parse_fb_post(post_text, api_key="fake_api_key", max_retries=3, retry_delay_seconds=0.01)

        assert "目前查詢人數較多" in str(exc_info.value)
        assert mock_chat.send_message.await_count == 3


@pytest.mark.anyio
async def test_parse_fb_post_keyword_normalization():
    """Test that extracted search_query_ja is properly cleaned with uppercase ASCII and normalized spaces."""
    post_text = "售 Sony wh-1000xm5"

    expected_payload = {
        "franchise": "Sony",
        "character": "WH-1000XM5",
        "item_type": "ヘッドホン",
        "year_or_edition": None,
        "search_query_ja": "  sony   wh-1000xm5\u3000\u3000ヘッドホン  ",
        "fb_price_twd": None,
        "is_anime_merch": True,
    }
    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.search_query_ja == "SONY WH-1000XM5 ヘッドホン"


@pytest.mark.anyio
async def test_parse_fb_post_generic_category_fallback():
    """Test that ambiguous or unknown product input returns deduced generic category without failing."""
    post_text = "求推薦這把桌球拍"

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "franchise": "桌球拍",
        "character": "桌球拍",
        "item_type": "卓球ラケット",
        "year_or_edition": None,
        "keyword_jp": "卓球ラケット",
        "keyword_zh": "桌球拍",
        "fb_price_twd": None,
        "is_anime_merch": True,
    })

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.keyword_jp == "卓球ラケット"
        assert result.keyword_zh == "桌球拍"
        assert result.is_anime_merch is True


@pytest.mark.anyio
async def test_parse_fb_post_empty_model_keywords_fallback():
    """Test that if model outputs empty keywords, it falls back to cleaned text without raising error."""
    post_text = "【出清】二手無名物品 100"

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "franchise": "",
        "character": "",
        "item_type": "",
        "year_or_edition": None,
        "keyword_jp": "",
        "keyword_zh": "",
        "fb_price_twd": None,
        "is_anime_merch": True,
    })

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.keyword_zh == "【出清】二手無名物品 100"
        assert result.keyword_jp == "【出清】二手無名物品 100"
        assert result.is_anime_merch is True


@pytest.mark.anyio
async def test_parse_fb_post_empty_input():
    """Test that empty or whitespace-only input raises IrrelevantPostError without calling API."""
    with pytest.raises(IrrelevantPostError):
        await parse_fb_post("   ", api_key="fake_api_key")


@pytest.mark.anyio
async def test_parse_fb_post_missing_api_key():
    """Test that missing GEMINI_API_KEY raises GeminiAPIError when complex or image input requires LLM."""
    complex_text = "【社團好物交流】朋友託售，九成新無盒裝，功能正常，意者留言私訊，感謝管理員放行！\n售 Sony 耳機 100"
    with patch("services.parser.settings.gemini_api_key", None), \
         patch("services.parser.os.getenv", return_value=None):
        with pytest.raises(GeminiAPIError) as exc_info:
            await parse_fb_post(complex_text, api_key=None)
        assert "GEMINI_API_KEY is not set" in str(exc_info.value)


@pytest.mark.anyio
async def test_parse_fb_post_api_failure():
    """Test that non-retryable upstream Gemini API failures raise GeminiAPIError gracefully."""
    complex_post_text = "【出清】誠可議價，歡迎面交或郵寄。\n售 Yonex 88D 拍子 3000"

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(
            side_effect=APIError(400, {"message": "Invalid argument", "status": "INVALID_ARGUMENT"})
        )

        with pytest.raises(GeminiAPIError) as exc_info:
            await parse_fb_post(complex_post_text, api_key="fake_api_key")

        assert "Gemini API error" in str(exc_info.value)


@pytest.mark.anyio
async def test_parse_fb_post_strict_core_keyword_simplicity():
    """Test that concise queries preserve core keywords without filler words or over-translation."""
    complex_post = "請幫我找一下這個任天堂的最新主機：\nSwitch 2"

    expected_payload = {
        "franchise": "Nintendo",
        "character": "Switch 2",
        "item_type": "ゲーム機",
        "year_or_edition": None,
        "keyword_jp": "Switch 2",
        "keyword_zh": "Switch 2",
        "fb_price_twd": None,
        "is_anime_merch": True,
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(expected_payload)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(complex_post, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.keyword_jp == "SWITCH 2"
        assert result.keyword_zh == "SWITCH 2"
        assert result.is_anime_merch is True


@pytest.mark.anyio
async def test_abbreviations_and_shorthand_expansion():
    """Test that shorthand abbreviations like 're:0' expand to full official titles via Gemini."""
    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat

        expected_re0 = {
            "franchise": "Re:ゼロから始める異世界生活",
            "character": "エミリア",
            "item_type": "フィギュア",
            "year_or_edition": None,
            "keyword_jp": "Re:ゼロから始める異世界生活 エミリア",
            "keyword_zh": "Re:從零開始的異世界生活 愛蜜莉雅",
            "fb_price_twd": 1200,
            "is_anime_merch": True,
        }
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(expected_re0)
        mock_chat.send_message = AsyncMock(return_value=mock_resp)

        complex_query = "【售】re:0 愛蜜莉雅 景品公仔 1200"
        result = await parse_fb_post(complex_query, api_key="fake_key")
        assert result.keyword_jp == "RE:ゼロから始める異世界生活 エミリア"
        assert result.keyword_zh == "RE:從零開始的異世界生活 愛蜜莉雅"


@pytest.mark.anyio
async def test_few_shot_cot_schema_with_reasoning():
    """Test that LLM outputs with reasoning, zh_keyword, and jp_keyword correctly populate ParsedItem."""
    few_shot_output = {
        "reasoning": "Refers to Satoru Gojo from the anime Jujutsu Kaisen.",
        "zh_keyword": "咒術迴戰 五條悟",
        "jp_keyword": "呪術廻戦 五条悟",
        "fb_price_twd": None,
        "is_anime_merch": True,
    }

    mock_resp = MagicMock()
    mock_resp.text = json.dumps(few_shot_output)

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(return_value=mock_resp)

        complex_text = "【收】五條 徽章 誠可議"
        result = await parse_fb_post(complex_text, api_key="fake_key")

        assert result.reasoning == "Refers to Satoru Gojo from the anime Jujutsu Kaisen."
        assert result.keyword_jp == "呪術廻戦 五条悟"
        assert result.keyword_zh == "咒術迴戰 五條悟"
        assert result.search_query_ja == "呪術廻戦 五条悟"
        assert result.is_anime_merch is True


@pytest.mark.anyio
async def test_chinese_queries_must_translate_to_native_japanese():
    """Test that Chinese queries are not blindly echoed to jp_keyword, but translated to native Japanese."""
    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat

        expected_llm = {
            "reasoning": "Manga title translated to Japanese.",
            "zh_keyword": "藍色監獄",
            "jp_keyword": "ブルーロック",
            "fb_price_twd": None,
            "is_anime_merch": True,
        }
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(expected_llm)
        mock_chat.send_message = AsyncMock(return_value=mock_resp)

        result = await parse_fb_post("藍色監獄", api_key="fake_key")
        assert result.keyword_jp == "ブルーロック"
        assert result.keyword_zh == "藍色監獄"
        assert result.search_query_ja == "ブルーロック"


def test_parsed_item_suggested_term_schema():
    """Test that ParsedItem schema supports suggested_term field."""
    from services.parser import ParsedItem

    # Default is None
    item_default = ParsedItem(
        franchise="Apple",
        character="iPhone 15",
    )
    assert item_default.suggested_term is None

    # Explicit suggested_term
    item_with_suggestion = ParsedItem(
        franchise="Apple",
        character="iPhone 15",
        suggested_term="Apple iPhone 15",
    )
    assert item_with_suggestion.suggested_term == "Apple iPhone 15"
    data = item_with_suggestion.model_dump()
    assert data["suggested_term"] == "Apple iPhone 15"


def test_parsed_item_perfected_keyword_schema():
    """Test that ParsedItem schema supports perfected_keyword field with fallback."""
    from services.parser import ParsedItem

    # Default falls back to keyword_zh or franchise + character
    item_default = ParsedItem(
        franchise="Nintendo",
        character="Switch",
    )
    assert item_default.perfected_keyword == "Nintendo Switch"

    # Explicit perfected_keyword
    item_explicit = ParsedItem(
        franchise="Nintendo",
        character="Switch",
        perfected_keyword="Nintendo Switch OLED",
    )
    assert item_explicit.perfected_keyword == "Nintendo Switch OLED"
    data = item_explicit.model_dump()
    assert data["perfected_keyword"] == "Nintendo Switch OLED"


def test_parsed_item_estimated_min_usd_schema():
    """Test that ParsedItem schema supports estimated_min_usd integer field."""
    from services.parser import ParsedItem

    # Default is None
    item_default = ParsedItem(
        franchise="Butterfly",
        character="Viscaria",
    )
    assert item_default.estimated_min_usd is None

    # Explicit integer value
    item_with_est = ParsedItem(
        franchise="Butterfly",
        character="Viscaria",
        estimated_min_usd=80,
    )
    assert item_with_est.estimated_min_usd == 80
    data = item_with_est.model_dump()
    assert data["estimated_min_usd"] == 80


@pytest.mark.anyio
async def test_parse_fb_post_with_estimated_min_usd():
    """Test that parse_fb_post correctly parses estimated_min_usd from Gemini output."""
    mock_chat = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "reasoning": "Butterfly Viscaria blade table tennis equipment.",
        "zh_keyword": "蝴蝶王",
        "jp_keyword": "ビスカリア",
        "franchise": "Butterfly",
        "character": "Viscaria",
        "item_type": "卓球ラケット",
        "fb_price_twd": 3500,
        "estimated_min_usd": 75,
        "is_anime_merch": True,
    })
    mock_chat.send_message = AsyncMock(return_value=mock_response)

    with patch("services.parser.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.aio.chats.create.return_value = mock_chat
        mock_client_cls.return_value = mock_client

        result = await parse_fb_post("售 蝴蝶王 FL $3500", api_key="fake_key")
        assert result.estimated_min_usd == 75
        assert result.keyword_jp == "ビスカリア"
        assert result.fb_price_twd == 3500


def _gemini_returning(payload):
    """Patch Gemini client so every chat returns the given JSON payload; yields the mock chat."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(payload)
    patcher = patch("services.parser.genai.Client")
    mock_client_class = patcher.start()
    mock_chat = MagicMock()
    mock_client_class.return_value.aio.chats.create.return_value = mock_chat
    mock_chat.send_message = AsyncMock(return_value=mock_response)
    return patcher, mock_client_class, mock_chat


@pytest.mark.anyio
@pytest.mark.parametrize(
    "user_input, payload, expected_name, expected_category",
    [
        (
            "AJ1",
            {"perfected_keyword": "Nike Air Jordan 1", "keyword_zh": "Nike Air Jordan 1",
             "keyword_jp": "ナイキ エアジョーダン1", "category": "服飾鞋包"},
            "Nike Air Jordan 1",
            "服飾鞋包",
        ),
        (
            "switch2",
            {"perfected_keyword": "Nintendo Switch 2", "keyword_zh": "Nintendo Switch 2",
             "keyword_jp": "Nintendo Switch 2", "category": "3C 家電"},
            "Nintendo Switch 2",
            "3C 家電",
        ),
        (
            "PS5",
            {"perfected_keyword": "Sony PlayStation 5", "keyword_zh": "PlayStation 5",
             "keyword_jp": "PlayStation 5", "category": "3C 家電"},
            "Sony PlayStation 5",
            "3C 家電",
        ),
    ],
)
async def test_model_codes_and_abbreviations_are_expanded_by_gemini(
    user_input, payload, expected_name, expected_category
):
    patcher, _, mock_chat = _gemini_returning(payload)
    try:
        result = await parse_fb_post(user_input, api_key="fake_key")
    finally:
        patcher.stop()

    mock_chat.send_message.assert_awaited_once_with(message=user_input.lower())
    assert result.perfected_keyword == expected_name
    assert result.category.value == expected_category


@pytest.mark.anyio
async def test_no_input_bypasses_gemini(monkeypatch):
    """Model codes like 'Switch 2' used to skip the LLM; now every input needs Gemini."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch("services.parser.settings.gemini_api_key", None):
        for text in ("Switch 2", "PS5", "蝴蝶王", "re:0"):
            with pytest.raises(GeminiAPIError):
                await parse_fb_post(text, api_key=None)


@pytest.mark.anyio
@pytest.mark.parametrize("raw_category", ["家具", "", None])
async def test_undeterminable_category_becomes_other(raw_category):
    payload = {"keyword_zh": "某商品", "keyword_jp": "ある商品"}
    if raw_category is not None:
        payload["category"] = raw_category
    patcher, _, _ = _gemini_returning(payload)
    try:
        result = await parse_fb_post("某商品", api_key="fake_key")
    finally:
        patcher.stop()

    assert result.category.value == "其他"


@pytest.mark.anyio
async def test_only_perfected_keyword_is_used_instead_of_the_raw_post():
    """gemini-3.6-flash 常只回 perfected_keyword；不可退回用原文（含「售」、價格）搜尋。"""
    payload = {"perfected_keyword": "Nintendo Switch 2", "category": "3C 家電"}
    patcher, _, _ = _gemini_returning(payload)
    try:
        result = await parse_fb_post("售 switch2 主機 台灣公司貨 12000", api_key="fake_key")
    finally:
        patcher.stop()

    assert result.keyword_zh == "NINTENDO SWITCH 2"
    assert result.keyword_jp == "NINTENDO SWITCH 2"


def test_gemini_schema_limits_category_to_six_values():
    schema = ParsedItem.model_json_schema()
    category_schema = schema["$defs"][schema["properties"]["category"]["$ref"].split("/")[-1]]
    assert set(category_schema["enum"]) == {"3C 家電", "美妝保養", "服飾鞋包", "動漫周邊/玩具", "運動戶外", "其他"}


# --- 時間上限（工作票 06）---

def _hanging_then(*responses):
    """第一次呼叫卡住不回應，之後依序回傳 responses。"""
    calls = iter([None, *responses])

    async def send_message(**kwargs):
        response = next(calls)
        if response is None:
            await asyncio.sleep(3600)
        return response

    return send_message


@pytest.mark.anyio
async def test_parse_fb_post_abandons_a_hanging_attempt_and_retries():
    success = MagicMock()
    success.text = json.dumps({"keyword_zh": "Switch 2", "category": "3C 家電"})

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_chat = MagicMock()
        mock_client_class.return_value.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(side_effect=_hanging_then(success))

        result = await parse_fb_post(
            "switch2", api_key="fake_api_key", attempt_timeout_seconds=0.01, retry_delay_seconds=0.01
        )

    assert result.keyword_zh == "SWITCH 2"
    assert mock_chat.send_message.await_count == 2


@pytest.mark.anyio
async def test_parse_fb_post_every_attempt_hanging_is_an_ai_busy_error():
    with patch("services.parser.genai.Client") as mock_client_class:
        mock_chat = MagicMock()
        mock_client_class.return_value.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(side_effect=_hanging_then(None, None))

        with pytest.raises(GeminiServerError):
            await parse_fb_post(
                "switch2", api_key="fake_api_key", attempt_timeout_seconds=0.01, retry_delay_seconds=0.01
            )

    assert mock_chat.send_message.await_count == 2


def test_parse_fb_post_default_budget_matches_the_comparison_ai_budget():
    """
    解析器的總時限等於比價流程給 AI 解析的時間；第一次嘗試比均分兩次長，
    且逾時後仍留得下一次重試（實測單次約 2 秒）。
    """
    import inspect

    from services.comparison import AI_PARSE_BUDGET_SECONDS

    defaults = {
        name: param.default for name, param in inspect.signature(parse_fb_post).parameters.items()
    }
    after_first_timeout = (
        defaults["total_timeout_seconds"] - defaults["attempt_timeout_seconds"] - defaults["retry_delay_seconds"]
    )

    assert defaults["total_timeout_seconds"] == AI_PARSE_BUDGET_SECONDS
    assert defaults["max_retries"] == 2
    assert defaults["attempt_timeout_seconds"] > AI_PARSE_BUDGET_SECONDS / 2
    assert after_first_timeout >= 2.0


@pytest.mark.anyio
async def test_parse_fb_post_retry_only_gets_the_remaining_total_budget():
    import time

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_chat = MagicMock()
        mock_client_class.return_value.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(side_effect=_hanging_then(None, None))

        started = time.monotonic()
        with pytest.raises(GeminiServerError):
            await parse_fb_post(
                "switch2", api_key="fake_api_key",
                attempt_timeout_seconds=0.2, retry_delay_seconds=0.05, total_timeout_seconds=0.3,
            )
        elapsed = time.monotonic() - started

    # 不設總時限時要 0.2 + 0.05 + 0.2 = 0.45 秒
    assert mock_chat.send_message.await_count == 2
    assert elapsed < 0.4


@pytest.mark.anyio
async def test_parse_fb_post_does_not_retry_once_the_total_budget_is_spent():
    with patch("services.parser.genai.Client") as mock_client_class:
        mock_chat = MagicMock()
        mock_client_class.return_value.aio.chats.create.return_value = mock_chat
        mock_chat.send_message = AsyncMock(side_effect=_hanging_then(None, None))

        with pytest.raises(GeminiServerError):
            await parse_fb_post(
                "switch2", api_key="fake_api_key",
                attempt_timeout_seconds=0.1, retry_delay_seconds=0.01, total_timeout_seconds=0.1,
            )

    assert mock_chat.send_message.await_count == 1


def _thinking_config_sent(monkeypatch, model, level=None):
    monkeypatch.setenv("GEMINI_MODEL", model)
    if level is None:
        monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    else:
        monkeypatch.setenv("GEMINI_THINKING_LEVEL", level)
    patcher, mock_client_class, _ = _gemini_returning({"keyword_zh": "Switch 2", "category": "3C 家電"})
    try:
        asyncio.run(parse_fb_post("switch2", api_key="fake_api_key"))
    finally:
        patcher.stop()
    config = mock_client_class.return_value.aio.chats.create.call_args.kwargs["config"]
    assert config.automatic_function_calling.disable is True
    return config.thinking_config


def test_gemini_3_models_think_at_low_level_by_default(monkeypatch):
    thinking = _thinking_config_sent(monkeypatch, "gemini-3.6-flash")
    assert thinking.thinking_level.value.lower() == "low"


def test_thinking_level_can_be_overridden_or_left_at_model_default(monkeypatch):
    assert _thinking_config_sent(monkeypatch, "gemini-3.6-flash", "high").thinking_level.value.lower() == "high"
    assert _thinking_config_sent(monkeypatch, "gemini-3.6-flash", "default") is None


def test_models_outside_gemini_3_keep_their_default_thinking(monkeypatch):
    assert _thinking_config_sent(monkeypatch, "gemini-flash-latest") is None
