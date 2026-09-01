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
    post_text = "底片相機"

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
        assert result.keyword_zh == "底片相機"
        assert result.keyword_jp == "底片相機"
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
async def test_parse_fb_post_fast_regex_bypass():
    """Test that standard queries bypass LLM completely via fast_regex_parse in < 0.1ms."""
    from services.parser import fast_regex_parse

    # 1. Clean query
    res1 = fast_regex_parse("Switch 2")
    assert res1 is not None
    assert res1.search_query_ja == "SWITCH 2"
    assert res1.fb_price_twd is None

    # 2. Product query
    res2 = fast_regex_parse("PS5")
    assert res2 is not None
    assert res2.search_query_ja == "PS5"

    # 3. Direct parse_fb_post bypass without API key
    res3 = await parse_fb_post("CCD 相機", api_key=None)
    assert res3.search_query_ja == "CCD 相機"


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

