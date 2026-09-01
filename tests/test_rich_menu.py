import os
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from create_rich_menu import (
    COL_WIDTH,
    ROW_HEIGHT,
    MENU_HEIGHT,
    MENU_WIDTH,
    create_rich_menu_request,
    deploy_rich_menu,
    generate_default_menu_image,
    resolve_image_path,
)


def test_create_rich_menu_request_structure():
    """Verify 3x2 grid RichMenuRequest dimensions (1680x944), bounds, and action assignments."""
    request = create_rich_menu_request()

    # Size
    assert request.size.width == 1680
    assert request.size.height == 944
    assert request.selected is True
    assert request.chat_bar_text == "點擊開啟選單"

    # Areas count
    assert len(request.areas) == 6

    # Area 1 (Top-Left): 一鍵尋寶體驗 (sends "Switch 2", x: 0, y: 0, width: 560, height: 472)
    a1 = request.areas[0]
    assert a1.bounds.x == 0
    assert a1.bounds.y == 0
    assert a1.bounds.width == 560
    assert a1.bounds.height == 472
    assert a1.action.type == "message"
    assert a1.action.text == "Switch 2"

    # Area 2 (Top-Center): 新手指南 (x: 560, y: 0, width: 560, height: 472)
    a2 = request.areas[1]
    assert a2.bounds.x == 560
    assert a2.bounds.y == 0
    assert a2.bounds.width == 560
    assert a2.bounds.height == 472
    assert a2.action.type == "message"
    assert a2.action.text == "新手指南"

    # Area 3 (Top-Right): Ezway認證 (x: 1120, y: 0, width: 560, height: 472)
    a3 = request.areas[2]
    assert a3.bounds.x == 1120
    assert a3.bounds.y == 0
    assert a3.bounds.width == 560
    assert a3.bounds.height == 472
    assert a3.action.type == "uri"
    assert a3.action.label == "Ezway認證"
    assert "https://web.customs.gov.tw/singlehtml/3150?cntId=cus1_3150_3150_1471" in a3.action.uri

    # Area 4 (Bottom-Left): 集運倉介紹 (x: 0, y: 472, width: 560, height: 472)
    a4 = request.areas[3]
    assert a4.bounds.x == 0
    assert a4.bounds.y == 472
    assert a4.bounds.width == 560
    assert a4.bounds.height == 472
    assert a4.action.type == "message"
    assert a4.action.text == "集運倉介紹"

    # Area 5 (Bottom-Center): 客服與回報 (x: 560, y: 472, width: 560, height: 472)
    a5 = request.areas[4]
    assert a5.bounds.x == 560
    assert a5.bounds.y == 472
    assert a5.bounds.width == 560
    assert a5.bounds.height == 472
    assert a5.action.type == "message"
    assert a5.action.text == "客服與回報"

    # Area 6 (Bottom-Right): 平台比較與免責 (x: 1120, y: 472, width: 560, height: 472)
    a6 = request.areas[5]
    assert a6.bounds.x == 1120
    assert a6.bounds.y == 472
    assert a6.bounds.width == 560
    assert a6.bounds.height == 472
    assert a6.action.type == "message"
    assert a6.action.text == "平台比較與免責"


def test_generate_default_menu_image(tmp_path):
    """Test image generation creates a valid 1680x944 PNG."""
    out_file = tmp_path / "test_menu.png"
    path_str = str(out_file)
    generate_default_menu_image(path_str)

    assert os.path.exists(path_str)
    img = Image.open(path_str)
    assert img.size == (1680, 944)
    assert img.format == "PNG"


def test_resolve_image_path(tmp_path):
    """Test image path resolution with custom path or fallback generation."""
    custom_img = tmp_path / "custom.png"
    generate_default_menu_image(str(custom_img))
    assert resolve_image_path(str(custom_img)) == str(custom_img)


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

    test_img = tmp_path / "一鍵尋寶體驗.png"
    generate_default_menu_image(str(test_img))

    token = "test_token_abc"
    result_id = deploy_rich_menu(channel_access_token=token, image_path=str(test_img))

    assert result_id == "richmenu-test-12345"
    mock_msg_api.create_rich_menu.assert_called_once()
    mock_blob_api.set_rich_menu_image.assert_called_once()
    mock_msg_api.set_default_rich_menu.assert_called_once_with(rich_menu_id="richmenu-test-12345")
