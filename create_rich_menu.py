#!/usr/bin/env python3
"""
Deploy a 6-grid custom LINE Rich Menu for cross-border e-commerce shopping helper.

Layout:
- Dimensions: 2500 x 1686 (3 rows x 2 columns, each cell 1250 x 562)
- Areas & Actions:
  - Row 1 (y: 0~562):
      - Area 1 (Left:  x: 0~1250,    y: 0~562):    MessageAction -> "一鍵尋寶體驗"
      - Area 2 (Right: x: 1250~2500, y: 0~562):    MessageAction -> "新手圖解指南"
  - Row 2 (y: 562~1124):
      - Area 3 (Left:  x: 0~1250,    y: 562~1124): URIAction     -> "EZ WAY 認證教學" (Customs URL)
      - Area 4 (Right: x: 1250~2500, y: 562~1124): URIAction     -> "集運與關稅說明" (Shipping/Tariff Guide)
  - Row 3 (y: 1124~1686):
      - Area 5 (Left:  x: 0~1250,    y: 1124~1686): MessageAction -> "法律免責聲明"
      - Area 6 (Right: x: 1250~2500, y: 1124~1686): URIAction     -> "客服與回報" (Customer Support URL)

Usage:
    python create_rich_menu.py [--image path/to/menu_image.jpg]
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessageAction,
    MessagingApi,
    MessagingApiBlob,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
    URIAction,
)
from PIL import Image, ImageDraw, ImageFont

# Load environment variables from .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rich_menu_deployer")

# Rich Menu Constants
MENU_WIDTH = 2500
MENU_HEIGHT = 1686
CELL_WIDTH = 1250
CELL_HEIGHT = 562

DEFAULT_EZWAY_URL = os.getenv(
    "RICH_MENU_EZWAY_URL",
    "https://web.customs.gov.tw/singlehtml/3150?cntId=cus1_3150_3150_1372",
)
DEFAULT_SHIPPING_GUIDE_URL = os.getenv(
    "RICH_MENU_SHIPPING_GUIDE_URL",
    "https://buyee.jp/help/yahoo/guide/shipping-fees",
)
DEFAULT_SUPPORT_URL = os.getenv(
    "RICH_MENU_SUPPORT_URL",
    "https://line.me/R/ti/p/@your_bot_id",
)


def create_rich_menu_request() -> RichMenuRequest:
    """Build the 6-grid RichMenuRequest specification with exact bounding boxes and actions."""
    areas = [
        # Row 1 Left: 一鍵尋寶體驗
        RichMenuArea(
            bounds=RichMenuBounds(x=0, y=0, width=CELL_WIDTH, height=CELL_HEIGHT),
            action=MessageAction(text="一鍵尋寶體驗", label="一鍵尋寶體驗"),
        ),
        # Row 1 Right: 新手圖解指南
        RichMenuArea(
            bounds=RichMenuBounds(x=CELL_WIDTH, y=0, width=CELL_WIDTH, height=CELL_HEIGHT),
            action=MessageAction(text="新手圖解指南", label="新手圖解指南"),
        ),
        # Row 2 Left: EZ WAY 認證教學
        RichMenuArea(
            bounds=RichMenuBounds(x=0, y=CELL_HEIGHT, width=CELL_WIDTH, height=CELL_HEIGHT),
            action=URIAction(uri=DEFAULT_EZWAY_URL, label="EZ WAY 認證教學"),
        ),
        # Row 2 Right: 集運與關稅說明
        RichMenuArea(
            bounds=RichMenuBounds(x=CELL_WIDTH, y=CELL_HEIGHT, width=CELL_WIDTH, height=CELL_HEIGHT),
            action=URIAction(uri=DEFAULT_SHIPPING_GUIDE_URL, label="集運與關稅說明"),
        ),
        # Row 3 Left: 法律免責聲明
        RichMenuArea(
            bounds=RichMenuBounds(x=0, y=CELL_HEIGHT * 2, width=CELL_WIDTH, height=CELL_HEIGHT),
            action=MessageAction(text="法律免責聲明", label="法律免責聲明"),
        ),
        # Row 3 Right: 客服與回報
        RichMenuArea(
            bounds=RichMenuBounds(x=CELL_WIDTH, y=CELL_HEIGHT * 2, width=CELL_WIDTH, height=CELL_HEIGHT),
            action=URIAction(uri=DEFAULT_SUPPORT_URL, label="客服與回報"),
        ),
    ]

    return RichMenuRequest(
        size=RichMenuSize(width=MENU_WIDTH, height=MENU_HEIGHT),
        selected=True,
        name="e_commerce_6grid_menu",
        chat_bar_text="點擊開啟選單",
        areas=areas,
    )


def generate_default_menu_image(output_path: str = "menu_image.jpg") -> str:
    """Generate a clean, aesthetic default 6-grid placeholder image if no custom image is supplied."""
    img = Image.new("RGB", (MENU_WIDTH, MENU_HEIGHT), color=(248, 249, 250))
    draw = ImageDraw.Draw(img)

    # Grid labels and colors
    cards = [
        # (row, col, title, subtitle, bg_color, text_color)
        (0, 0, "🔍 一鍵尋寶體驗", "貼上網址 / 關鍵字即時比價", (230, 246, 236), (18, 140, 70)),
        (0, 1, "📖 新手圖解指南", "輕鬆掌握台日網購省錢秘笈", (238, 242, 255), (67, 56, 202)),
        (1, 0, "🛃 EZ WAY 認證教學", "海外進口報關實名委任教學", (254, 243, 199), (180, 83, 9)),
        (1, 1, "📦 集運與關稅說明", "各大代購平台國際運費行情", (255, 237, 213), (194, 65, 12)),
        (2, 0, "⚖️ 法律免責聲明", "匯率即時試算與數據說明", (241, 245, 249), (71, 85, 105)),
        (2, 1, "💬 客服與回報", "功能反饋與線上客服聯絡", (252, 231, 243), (190, 24, 93)),
    ]

    # Try loading a system TTF font, fallback to default font
    try:
        font_title = ImageFont.truetype("Arial.ttf", 68)
        font_sub = ImageFont.truetype("Arial.ttf", 40)
    except IOError:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    padding = 24
    for r, c, title, subtitle, bg_col, text_col in cards:
        x0 = c * CELL_WIDTH + padding
        y0 = r * CELL_HEIGHT + padding
        x1 = (c + 1) * CELL_WIDTH - padding
        y1 = (r + 1) * CELL_HEIGHT - padding

        # Draw card container
        draw.rounded_rectangle([x0, y0, x1, y1], radius=32, fill=bg_col, outline=(226, 232, 240), width=4)

        # Draw text
        text_x = x0 + 60
        text_y = y0 + 180
        draw.text((text_x, text_y), title, fill=text_col, font=font_title)
        draw.text((text_x, text_y + 110), subtitle, fill=(100, 116, 139), font=font_sub)

    img.save(output_path, format="JPEG", quality=92)
    logger.info(f"🎨 Generated default Rich Menu image at: {output_path}")
    return output_path


def deploy_rich_menu(
    channel_access_token: str,
    image_path: str = "menu_image.jpg",
) -> str:
    """
    Deploy the 6-grid Rich Menu to LINE Messaging API:
    1. Create Rich Menu definition -> rich_menu_id
    2. Upload menu background image (JPEG/PNG)
    3. Set as default Rich Menu for all users
    """
    configuration = Configuration(access_token=channel_access_token)

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_blob_api = MessagingApiBlob(api_client)

        # 1. Create Rich Menu
        logger.info("🚀 Step 1/3: Creating Rich Menu definition via LINE API...")
        menu_request = create_rich_menu_request()
        response = messaging_api.create_rich_menu(rich_menu_request=menu_request)
        rich_menu_id = response.rich_menu_id
        logger.info(f"✅ Rich Menu created successfully! ID: {rich_menu_id}")

        # 2. Upload Image
        if not os.path.exists(image_path):
            logger.warning(f"⚠️ Image file '{image_path}' not found. Auto-generating default template image...")
            generate_default_menu_image(image_path)

        logger.info(f"📤 Step 2/3: Uploading menu image '{image_path}'...")
        with open(image_path, "rb") as fp:
            image_bytes = fp.read()

        content_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        messaging_blob_api.set_rich_menu_image(
            rich_menu_id=rich_menu_id,
            body=image_bytes,
            _headers={"Content-Type": content_type},
        )
        logger.info("✅ Rich Menu image uploaded successfully!")

        # 3. Set as Default Menu
        logger.info("🌟 Step 3/3: Setting as default Rich Menu for all users...")
        messaging_api.set_default_rich_menu(rich_menu_id=rich_menu_id)
        logger.info(f"🎉 Default Rich Menu active! Users will now see this menu (ID: {rich_menu_id})")

        return rich_menu_id


def main():
    parser = argparse.ArgumentParser(description="Construct and deploy a 6-grid custom LINE Rich Menu.")
    parser.add_argument(
        "--image",
        default="menu_image.jpg",
        help="Path to the rich menu image (2500x1686 px, JPEG/PNG). Auto-generated if not found.",
    )
    args = parser.parse_args()

    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token or not token.strip():
        logger.error(
            "❌ Missing required environment variable 'LINE_CHANNEL_ACCESS_TOKEN'.\n"
            "Please configure LINE_CHANNEL_ACCESS_TOKEN in your .env file or environment."
        )
        sys.exit(1)

    try:
        rich_menu_id = deploy_rich_menu(channel_access_token=token, image_path=args.image)
        print(f"\n==========================================")
        print(f"✨ Rich Menu Deployment Completed!")
        print(f"🆔 Rich Menu ID: {rich_menu_id}")
        print(f"==========================================\n")
    except Exception as exc:
        logger.error(f"❌ Failed to deploy Rich Menu: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
