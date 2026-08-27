import json
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import pytest
from google.genai.errors import APIError

from services.parser import (
    GeminiAPIError,
    GeminiRateLimitError,
    IrrelevantPostError,
    ParsedItem,
    parse_fb_post,
    resolve_model_name,
)


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
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert result.character == "WH-1000XM5"
        assert result.item_type == "ヘッドホン"
        assert result.search_query_ja == "Sony WH-1000XM5 ヘッドホン"
        assert result.fb_price_twd == 8500
        assert result.is_anime_merch is True

        mock_client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.anyio
async def test_parse_fb_post_image_only_success():
    """Test image-only multimodal recognition with raw bytes."""
    fake_image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF..."

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
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(image_data=fake_image_bytes, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Canon"
        assert result.character == "EOS R6 Mark II"
        assert result.search_query_ja == "Canon EOS R6 Mark II ミラーレス一眼"
        assert result.is_anime_merch is True

        mock_client.aio.models.generate_content.assert_awaited_once()
        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        assert len(call_kwargs["contents"]) == 2


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
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text=post_text, image_data=img, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Nike"
        assert result.character == "Air Jordan 1 Retro High OG"
        assert result.search_query_ja == "Nike Air Jordan 1 Chicago スニーカー"

        mock_client.aio.models.generate_content.assert_awaited_once()


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
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "ハイキュー!!"
        assert result.character == "影山飛雄"
        assert result.item_type == "もちもちマスコット"
        assert result.year_or_edition == "2020"
        assert result.search_query_ja == "ハイキュー 影山飛雄 もちもちマスコット 2020"
        assert result.fb_price_twd == 1500
        assert result.is_anime_merch is True

        mock_client.aio.models.generate_content.assert_awaited_once()


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
        # First call with nonexistent model fails with 404, second call with gemini-flash-latest succeeds
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[error_404, mock_success_response]
        )

        result = await parse_fb_post(post_text, api_key="fake_api_key")

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert mock_client.aio.models.generate_content.await_count == 2
        # Verify second call used gemini-flash-latest
        second_call_kwargs = mock_client.aio.models.generate_content.call_args_list[1].kwargs
        assert second_call_kwargs["model"] == "gemini-flash-latest"


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

    with patch("services.parser.genai.Client") as mock_client_class, \
         patch("services.parser.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        # First call fails with 429, second call succeeds
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[rate_limit_error, mock_success_response]
        )

        result = await parse_fb_post(post_text, api_key="fake_api_key", retry_delay_seconds=0.01)

        assert isinstance(result, ParsedItem)
        assert result.franchise == "Sony"
        assert mock_client.aio.models.generate_content.await_count == 2
        mock_sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_parse_fb_post_rate_limit_exceeded():
    """Test that persistent 429 rate limit raises GeminiRateLimitError after 3 retries."""
    post_text = "售 Sony WH-1000XM5 8000"
    rate_limit_error = APIError(429, {"message": "RESOURCE_EXHAUSTED", "status": "RESOURCE_EXHAUSTED"})

    with patch("services.parser.genai.Client") as mock_client_class, \
         patch("services.parser.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.aio.models.generate_content = AsyncMock(side_effect=rate_limit_error)

        with pytest.raises(GeminiRateLimitError) as exc_info:
            await parse_fb_post(post_text, api_key="fake_api_key", max_retries=3, retry_delay_seconds=0.01)

        assert "目前查詢人數較多" in str(exc_info.value)
        assert mock_client.aio.models.generate_content.await_count == 3
        assert mock_sleep.await_count == 2


@pytest.mark.anyio
async def test_parse_fb_post_irrelevant_text():
    """Test that irrelevant non-goods text raises IrrelevantPostError with generic message."""
    post_text = "今天天氣真好，大家要去哪裡玩？"

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "franchise": "",
        "character": "",
        "item_type": "",
        "year_or_edition": None,
        "search_query_ja": "",
        "fb_price_twd": None,
        "is_anime_merch": False,
    })

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with pytest.raises(IrrelevantPostError) as exc_info:
            await parse_fb_post(post_text, api_key="fake_api_key")

        assert "無法解析此貼文" in str(exc_info.value)


@pytest.mark.anyio
async def test_parse_fb_post_empty_input():
    """Test that empty or whitespace-only input raises IrrelevantPostError without calling API."""
    with pytest.raises(IrrelevantPostError):
        await parse_fb_post("   ", api_key="fake_api_key")


@pytest.mark.anyio
async def test_parse_fb_post_missing_api_key():
    """Test that missing GEMINI_API_KEY raises GeminiAPIError."""
    with patch("services.parser.settings.gemini_api_key", None), \
         patch("services.parser.os.getenv", return_value=None):
        with pytest.raises(GeminiAPIError) as exc_info:
            await parse_fb_post("售 Sony 耳機 100", api_key=None)
        assert "GEMINI_API_KEY is not set" in str(exc_info.value)


@pytest.mark.anyio
async def test_parse_fb_post_api_failure():
    """Test that upstream Gemini API failures raise GeminiAPIError gracefully."""
    post_text = "售 Yonex 88D 拍子 3000"

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=APIError(500, {"message": "Service unavailable", "status": "INTERNAL"})
        )

        with pytest.raises(GeminiAPIError) as exc_info:
            await parse_fb_post(post_text, api_key="fake_api_key")

        assert "Gemini API error" in str(exc_info.value)
