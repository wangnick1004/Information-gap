import os
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from create_rich_menu import (
    CELL_HEIGHT,
    CELL_WIDTH,
    MENU_HEIGHT,
    MENU_WIDTH,
    create_rich_menu_request,
    deploy_rich_menu,
    generate_default_menu_image,
)


def test_create_rich_menu_request_structure():
    """Verify 6-grid RichMenuRequest dimensions, bounds, and action assignments."""
    request = create_rich_menu_request()

    # Size
    assert request.size.width == 2500
    assert request.size.height == 1686
    assert request.selected is True
    assert request.chat_bar_text == "點擊開啟選單"

    # Areas count
    assert len(request.areas) == 6

    # Area 1 (Row 1 Left): 一鍵尋寶體驗
    a1 = request.areas[0]
    assert a1.bounds.x == 0
    assert a1.bounds.y == 0
    assert a1.bounds.width == 1250
    assert a1.bounds.height == 562
    assert a1.action.type == "message"
    assert a1.action.text == "一鍵尋寶體驗"

    # Area 2 (Row 1 Right): 新手圖解指南
    a2 = request.areas[1]
    assert a2.bounds.x == 1250
    assert a2.bounds.y == 0
    assert a2.bounds.width == 1250
    assert a2.bounds.height == 562
    assert a2.action.type == "message"
    assert a2.action.text == "新手圖解指南"

    # Area 3 (Row 2 Left): EZ WAY 認證教學
    a3 = request.areas[2]
    assert a3.bounds.x == 0
    assert a3.bounds.y == 562
    assert a3.bounds.width == 1250
    assert a3.bounds.height == 562
    assert a3.action.type == "uri"
    assert "customs.gov.tw" in a3.action.uri

    # Area 4 (Row 2 Right): 集運與關稅說明
    a4 = request.areas[3]
    assert a4.bounds.x == 1250
    assert a4.bounds.y == 562
    assert a4.bounds.width == 1250
    assert a4.bounds.height == 562
    assert a4.action.type == "uri"
    assert "shipping-fees" in a4.action.uri

    # Area 5 (Row 3 Left): 法律免責聲明
    a5 = request.areas[4]
    assert a5.bounds.x == 0
    assert a5.bounds.y == 1124
    assert a5.bounds.width == 1250
    assert a5.bounds.height == 562
    assert a5.action.type == "message"
    assert a5.action.text == "法律免責聲明"

    # Area 6 (Row 3 Right): 客服與回報
    a6 = request.areas[5]
    assert a6.bounds.x == 1250
    assert a6.bounds.y == 1124
    assert a6.bounds.width == 1250
    assert a6.bounds.height == 562
    assert a6.action.type == "uri"


def test_generate_default_menu_image(tmp_path):
    """Test image generation creates a valid 2500x1686 JPEG."""
    out_file = tmp_path / "test_menu.jpg"
    path_str = str(out_file)
    generate_default_menu_image(path_str)

    assert os.path.exists(path_str)
    img = Image.open(path_str)
    assert img.size == (2500, 1686)
    assert img.format == "JPEG"


@patch("create_rich_menu.MessagingApiBlob")
@patch("create_rich_menu.MessagingApi")
@patch("create_rich_menu.ApiClient")
def test_deploy_rich_menu_pipeline(mock_api_client_cls, mock_messaging_api_cls, mock_messaging_blob_cls, tmp_path):
    """Test 3-step deployment pipeline: create -> upload image -> set default."""
    mock_msg_api = MagicMock()
    mock_blob_api = MagicMock()
    mock_messaging_api_cls.return_value = mock_msg_api
    mock_messaging_blob_cls.return_value = mock_blob_api

    mock_resp = MagicMock()
    mock_resp.rich_menu_id = "richmenu-test-12345"
    mock_msg_api.create_rich_menu.return_value = mock_resp

    test_img = tmp_path / "menu_image.jpg"
    generate_default_menu_image(str(test_img))

    token = "test_token_abc"
    result_id = deploy_rich_menu(channel_access_token=token, image_path=str(test_img))

    assert result_id == "richmenu-test-12345"
    mock_msg_api.create_rich_menu.assert_called_once()
    mock_blob_api.set_rich_menu_image.assert_called_once()
    mock_msg_api.set_default_rich_menu.assert_called_once_with(rich_menu_id="richmenu-test-12345")
