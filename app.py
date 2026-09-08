"""
Main application alias for main.py (FastAPI application & LINE webhook).
Provides entrypoint and re-exports app, GEMINI_VISION_PROMPT, and handlers.
"""

from main import (  # noqa: F401
    GEMINI_VISION_PROMPT,
    app,
    handler,
    line_bot_api,
    line_bot_blob_api,
    parser,
)

# Re-export all public symbols from main
from main import *  # noqa: F401, F403
