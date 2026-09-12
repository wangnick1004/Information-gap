"""
Main application alias for main.py (FastAPI application & LINE webhook).
Provides entrypoint and re-exports app, GEMINI_VISION_PROMPT, and handlers.
"""

from main import (  # noqa: F401
    DynamicPriceResult,
    GEMINI_VISION_PROMPT,
    MessageAction,
    QuickReply,
    QuickReplyItem,
    TextSendMessage,
    app,
    calculate_dynamic_platform_prices,
    convert_to_twd,
    handler,
    line_bot_api,
    line_bot_blob_api,
    parser,
    remove_outliers,
)

# Re-export all public symbols from main
from main import *  # noqa: F401, F403
