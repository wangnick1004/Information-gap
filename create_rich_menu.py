#!/usr/bin/env python3
"""
Deploy a 3x2 grid custom LINE Rich Menu (1680x944 px) for cross-border e-commerce shopping helper.

Layout:
- Dimensions: 1680 x 944 (2 rows x 3 columns, each cell 560 x 472)
- Areas & Actions:
  - Row 1 (Top, y: 0~472):
      - Area 1 (Top-Left:   x: 0,    y: 0, width: 560, height: 472): MessageAction -> "Switch 2" (一鍵尋寶體驗)
      - Area 2 (Top-Center: x: 560,  y: 0, width: 560, height: 472): MessageAction -> "新手指南"
      - Area 3 (Top-Right:  x: 1120, y: 0, width: 560, height: 472): URIAction     -> "Ezway認證" (Customs URL)
  - Row 2 (Bottom, y: 472~944):
      - Area 4 (Bottom-Left:   x: 0,    y: 472, width: 560, height: 472): MessageAction -> "集運倉介紹"
      - Area 5 (Bottom-Center: x: 560,  y: 472, width: 560, height: 472): MessageAction -> "客服與回報"
      - Area 6 (Bottom-Right:  x: 1120, y: 472, width: 560, height: 472): MessageAction -> "平台比較與免責"

Usage:
    python create_rich_menu.py [--image path/to/image.png]
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

# Rich Menu Constants (3x2 Grid: 1680x944)
MENU_WIDTH = 1680
MENU_HEIGHT = 944
COL_WIDTH = 560
ROW_HEIGHT = 472

# Candidate Image Filenames to Auto-detect
DEFAULT_IMAGE_CANDIDATES = [
    "一鍵尋寶體驗.png",
    "一鍵尋寶體驗.jpg",
    "menu_image.png",
    "menu_image.jpg",
]

DEFAULT_EZWAY_URL = os.getenv(
    "RICH_MENU_EZWAY_URL",
    "https://web.customs.gov.tw/singlehtml/3150?cntId=cus1_3150_3150_1471",
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
    """Build the 3x2 grid RichMenuRequest specification with exact bounding boxes and actions."""
    areas = [
        # Area 1 (Top-Left): 一鍵尋寶體驗 (sends real search keyword "Switch 2")
        RichMenuArea(
            bounds=RichMenuBounds(x=0, y=0, width=COL_WIDTH, height=ROW_HEIGHT),
            action=MessageAction(text="Switch 2", label="一鍵尋寶體驗"),
        ),
        # Area 2 (Top-Center): 新手指南
        RichMenuArea(
            bounds=RichMenuBounds(x=COL_WIDTH, y=0, width=COL_WIDTH, height=ROW_HEIGHT),
            action=MessageAction(text="新手指南", label="新手指南"),
        ),
        # Area 3 (Top-Right): Ezway認證
        RichMenuArea(
            bounds=RichMenuBounds(x=COL_WIDTH * 2, y=0, width=COL_WIDTH, height=ROW_HEIGHT),
            action=URIAction(uri=DEFAULT_EZWAY_URL, label="Ezway認證"),
        ),
        # Area 4 (Bottom-Left): 集運倉介紹
        RichMenuArea(
            bounds=RichMenuBounds(x=0, y=ROW_HEIGHT, width=COL_WIDTH, height=ROW_HEIGHT),
            action=MessageAction(text="集運倉介紹", label="集運倉介紹"),
        ),
        # Area 5 (Bottom-Center): 客服與回報
        RichMenuArea(
            bounds=RichMenuBounds(x=COL_WIDTH, y=ROW_HEIGHT, width=COL_WIDTH, height=ROW_HEIGHT),
            action=MessageAction(text="客服與回報", label="客服與回報"),
        ),
        # Area 6 (Bottom-Right): 平台比較與免責
        RichMenuArea(
            bounds=RichMenuBounds(x=COL_WIDTH * 2, y=ROW_HEIGHT, width=COL_WIDTH, height=ROW_HEIGHT),
            action=MessageAction(text="平台比較與免責", label="平台比較與免責"),
        ),
    ]

    return RichMenuRequest(
        size=RichMenuSize(width=MENU_WIDTH, height=MENU_HEIGHT),
        selected=True,
        name="e_commerce_3x2_menu",
        chat_bar_text="點擊開啟選單",
        areas=areas,
    )


def generate_default_menu_image(output_path: str = "一鍵尋寶體驗.png") -> str:
    """Generate a clean, aesthetic default 3x2 grid placeholder image (1680x944) if no custom image is supplied."""
    img = Image.new("RGB", (MENU_WIDTH, MENU_HEIGHT), color=(248, 249, 250))
    draw = ImageDraw.Draw(img)

    # 3 columns x 2 rows cards
    cards = [
        # (row, col, title, subtitle, bg_color, text_color)
        (0, 0, "🔍 一鍵尋寶體驗", "貼上網址 / 關鍵字即時比價", (230, 246, 236), (18, 140, 70)),
        (0, 1, "📖 新手指南", "輕鬆掌握台日網購省錢秘笈", (238, 242, 255), (67, 56, 202)),
        (0, 2, "🛃 Ezway認證", "海外進口報關實名委任教學", (254, 243, 199), (180, 83, 9)),
        (1, 0, "📦 集運倉介紹", "各大代購平台國際運費行情", (255, 237, 213), (194, 65, 12)),
        (1, 1, "💬 客服與回報", "功能反饋與線上客服聯絡", (252, 231, 243), (190, 24, 93)),
        (1, 2, "⚖️ 平台比較與免責", "匯率即時試算與數據說明", (241, 245, 249), (71, 85, 105)),
    ]

    # Try loading a system TTF font, fallback to default font
    try:
        font_title = ImageFont.truetype("Arial.ttf", 42)
        font_sub = ImageFont.truetype("Arial.ttf", 24)
    except IOError:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    padding = 16
    for r, c, title, subtitle, bg_col, text_col in cards:
        x0 = c * COL_WIDTH + padding
        y0 = r * ROW_HEIGHT + padding
        x1 = (c + 1) * COL_WIDTH - padding
        y1 = (r + 1) * ROW_HEIGHT - padding

        # Draw card container
        draw.rounded_rectangle([x0, y0, x1, y1], radius=24, fill=bg_col, outline=(226, 232, 240), width=3)

        # Draw text
        text_x = x0 + 36
        text_y = y0 + 150
        draw.text((text_x, text_y), title, fill=text_col, font=font_title)
        draw.text((text_x, text_y + 70), subtitle, fill=(100, 116, 139), font=font_sub)

    fmt = "PNG" if output_path.lower().endswith(".png") else "JPEG"
    img.save(output_path, format=fmt, quality=92)
    logger.info(f"🎨 Generated default Rich Menu image at: {output_path}")
    return output_path


def resolve_image_path(specified_path: Optional[str] = None) -> str:
    """Resolve which image file to upload, checking specified path or known candidate names."""
    if specified_path and os.path.exists(specified_path):
        return specified_path

    for candidate in DEFAULT_IMAGE_CANDIDATES:
        if os.path.exists(candidate):
            logger.info(f"🔍 Found local Rich Menu image: '{candidate}'")
            return candidate

    fallback_path = specified_path or DEFAULT_IMAGE_CANDIDATES[0]
    logger.warning(f"⚠️ No existing rich menu image found. Auto-generating template at '{fallback_path}'...")
    generate_default_menu_image(fallback_path)
    return fallback_path


def deploy_rich_menu(
    channel_access_token: str,
    image_path: Optional[str] = None,
) -> str:
    """
    Deploy the 3x2 grid Rich Menu (1680x944 px) to LINE Messaging API:
    1. Create Rich Menu definition -> rich_menu_id
    2. Upload menu background image (JPEG/PNG)
    3. Set as default Rich Menu for all users
    """
    configuration = Configuration(access_token=channel_access_token)

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_blob_api = MessagingApiBlob(api_client)

        # 1. Create Rich Menu
        logger.info("🚀 Step 1/3: Creating Rich Menu definition (1680x944 3x2 grid) via LINE API...")
        menu_request = create_rich_menu_request()
        response = messaging_api.create_rich_menu(rich_menu_request=menu_request)
        rich_menu_id = response.rich_menu_id
        logger.info(f"✅ Rich Menu created successfully! ID: {rich_menu_id}")

        # 2. Upload Image
        resolved_img = resolve_image_path(image_path)
        logger.info(f"📤 Step 2/3: Uploading menu image '{resolved_img}'...")
        with open(resolved_img, "rb") as fp:
            image_bytes = fp.read()

        content_type = "image/png" if resolved_img.lower().endswith(".png") else "image/jpeg"
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
    parser = argparse.ArgumentParser(description="Construct and deploy a 3x2 grid (1680x944 px) custom LINE Rich Menu.")
    parser.add_argument(
        "--image",
        default=None,
        help="Path to rich menu image (1680x944 px, PNG/JPEG). Defaults to checking '一鍵尋寶體驗.png' / 'menu_image.jpg'.",
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
        print(f"📐 Resolution: 1680 x 944 (3 columns x 2 rows)")
        print(f"🆔 Rich Menu ID: {rich_menu_id}")
        print(f"==========================================\n")
    except Exception as exc:
        logger.error(f"❌ Failed to deploy Rich Menu: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
