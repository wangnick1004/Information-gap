import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai.errors import APIError

from services.parser import (
    GeminiAPIError,
    IrrelevantPostError,
    ParsedAnimeItem,
    parse_fb_post,
)


@pytest.mark.anyio
async def test_parse_fb_post_success():
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

        assert isinstance(result, ParsedAnimeItem)
        assert result.franchise == "ハイキュー!!"
        assert result.character == "影山飛雄"
        assert result.item_type == "もちもちマスコット"
        assert result.year_or_edition == "2020"
        assert result.search_query_ja == "ハイキュー 影山飛雄 もちもちマスコット 2020"
        assert result.fb_price_twd == 1500
        assert result.is_anime_merch is True

        # Verify generate_content was called with model gemini-1.5-flash
        mock_client.aio.models.generate_content.assert_awaited_once()
        call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-1.5-flash"
        assert call_kwargs["contents"] == post_text


@pytest.mark.anyio
async def test_parse_fb_post_irrelevant_text():
    """Test that irrelevant non-merchandise text raises IrrelevantPostError."""
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

        assert "recognizable anime merchandise" in str(exc_info.value)


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
            await parse_fb_post("售 排少 徽章 100", api_key=None)
        assert "GEMINI_API_KEY is not set" in str(exc_info.value)


@pytest.mark.anyio
async def test_parse_fb_post_api_failure():
    """Test that upstream Gemini API failures raise GeminiAPIError gracefully."""
    post_text = "售 咒術 五條悟 徽章 200"

    with patch("services.parser.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=APIError(500, {"message": "Service unavailable", "status": "INTERNAL"})
        )

        with pytest.raises(GeminiAPIError) as exc_info:
            await parse_fb_post(post_text, api_key="fake_api_key")

        assert "Gemini API error" in str(exc_info.value)
