import asyncio
import io
import json
import logging
import os
from typing import Any, List, Optional, Union

from google import genai
from google.genai import types
from google.genai.errors import APIError
from PIL import Image
from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger("line_bot.parser")


class ParsedItem(BaseModel):
    """Structured entity extraction schema for physical retail goods trading posts."""

    franchise: str = Field(
        default="",
        description="The core brand, manufacturer, or IP/series name translated/formatted for Japanese search (e.g., 'Sony', 'Yonex', 'Nikon', 'ハイキュー!!', 'Pokemon').",
    )
    character: str = Field(
        default="",
        description="The model name, character name, or specific product designation (e.g., 'WH-1000XM5', 'ASTROX 88D PRO', 'D850', '影山飛雄', 'リザードン').",
    )
    item_type: str = Field(
        default="",
        description="The product category or item type in standard Japanese (e.g., 'ヘッドホン', 'バドミントンラケット', '一眼レフカメラ', '缶バッジ', 'フィギュア', 'スニーカー').",
    )
    year_or_edition: Optional[str] = Field(
        default=None,
        description="Special edition, version, generation, or release year if mentioned (e.g., 'Mark II', '2024', 'PRO', '限定版').",
    )
    search_query_ja: str = Field(
        default="",
        description="The combined concise and precise Japanese search query for Japanese marketplaces (Mercari / Yahoo Auctions via Buyee).",
    )
    fb_price_twd: Optional[int] = Field(
        default=None,
        description="The extracted selling price in TWD (integer) from the Facebook post, or null if not found.",
    )
    is_anime_merch: bool = Field(
        default=True,
        description="True if the post describes any physical tradeable retail goods; False if irrelevant, spam, general text, or lacks identifiable product info.",
    )


# Alias for backward compatibility across modules
ParsedAnimeItem = ParsedItem


class ParsingError(Exception):
    """Base exception for parsing errors."""
    pass


class IrrelevantPostError(ParsingError):
    """Raised when the text/image lacks recognizable product details or is irrelevant."""
    pass


class GeminiAPIError(ParsingError):
    """Raised when the Gemini API call fails or credentials are missing."""
    pass


class GeminiRateLimitError(GeminiAPIError):
    """Raised when Gemini API rate limits (429 RESOURCE_EXHAUSTED) are exceeded after retries."""
    pass


SYSTEM_INSTRUCTION = """
You are an expert in Japanese cross-border commerce, secondhand market valuation (Yahoo! Auctions, Mercari, Rakuten, Buyee), and secondary market trading identification from text and product images.
Your mission is to analyze trading posts or product photos for ANY physical retail goods (e.g., consumer electronics, audio gear, cameras, badminton/sports equipment, sneakers, trading cards, anime merchandise, collectibles, watches, etc.) and extract structured product details translated into clean, search-effective Japanese keywords.

### Multimodal Analysis Instructions:
- Analyze the provided image (and text if any) to identify the specific physical retail item, its brand, model, and item type. Translate this into a precise Japanese search query for e-commerce platforms like Buyee/Mercari.
- If an image is provided, inspect logos, packaging text, labels, model numbers, barcodes, character visual traits, colorways, or device physical form factors.

### Guidelines & Domain Knowledge:
1. **Trading Slang & Noise (MUST IGNORE when forming search queries)**:
   - Transaction actions: 售 (sell), 收 (buy/WTT), 換 (trade), 降價 (price drop), 誠可議 (negotiable), 出清 (clearance), 回血 (fund recovery), 退坑.
   - Condition & accessories: 全新 (brand new), 95成新 (like new), 9成新, 二手 (used), 附發票 (with receipt), 盒裝完整 (complete in box), 原廠配件 (original accessories), 默認初傷, 微瑕.
   - Bundling & logistics: 綁 (bundle), 不拆 (no split), 拆售, 雙北面交 (meetup), 賣貨便, 運費另計.
   - NEVER include these transaction/condition terms in `search_query_ja`.

2. **Entity Extraction Rules**:
   - `franchise`: Core Brand, Manufacturer, or IP Franchise (e.g., Sony, Canon, Nikon, Yonex, Victor, Apple, Nintendo, Nike, Bandai, Pokémon, ハイキュー!!).
   - `character`: Model Name, Specific Product Name, Character, or Sub-line (e.g., WH-1000XM5, EOS R6, D850, ASTROX 88D, Air Jordan 1, 影山飛雄, リザードン).
   - `item_type`: Product Category in standard Japanese (e.g., ヘッドホン, バドミントンラケット, ミラーレス一眼カメラ, スニーカー, 缶バッジ, フィギュア, トレカ).
   - `year_or_edition`: Generation, version, or year if it is critical for distinguishing the product (e.g., Mark II, Gen 2, 2024).

3. **Search Query Construction (`search_query_ja`)**:
   - Combine: `[Brand / Franchise] [Model / Product Name / Character] [Item Category] [Edition/Year if applicable]` separated by single spaces.
   - Ensure the search query is concise, accurate, and optimized for Japanese marketplace search engines (Mercari, Yahoo Auctions).
   - Keep global brand names (e.g., Sony, Yonex, Canon, Apple) in their standard Latin/Japanese form commonly used on Japanese retail sites.

4. **Price Extraction (`fb_price_twd`)**:
   - Extract the target item's selling price as an integer in TWD (e.g., '1500', '$1500', '1500元', 'NT$1500' -> 1500).
   - If no price is mentioned or it is purely an image/inquiry without price, set to null.

5. **Relevance Flag (`is_anime_merch` / is_valid_goods)**:
   - Set `is_anime_merch: true` if the input describes or depicts ANY valid physical retail product.
   - Set `is_anime_merch: false` ONLY if the input is general conversational text, irrelevant scenery/memes, job ads, or lacks identifiable physical goods.
""".strip()


async def parse_fb_post(
    post_text: Optional[str] = None,
    image_data: Optional[Union[bytes, Image.Image]] = None,
    mime_type: str = "image/jpeg",
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay_seconds: float = 3.0,
) -> ParsedItem:
    """
    Parse a physical goods trading post or image using Gemini multimodal structured output,
    with automatic retry on 429 RESOURCE_EXHAUSTED rate limits.

    Args:
        post_text: Optional text or caption from user/post.
        image_data: Optional raw bytes or PIL Image object of the product image.
        mime_type: MIME type of the image if bytes (default "image/jpeg").
        api_key: Optional Gemini API key (defaults to settings/environment).
        max_retries: Maximum number of retry attempts for 429 rate limit errors (default 3).
        retry_delay_seconds: Delay before retrying in seconds (default 3.0s).

    Returns:
        ParsedItem: Structured entity extraction result.

    Raises:
        IrrelevantPostError: When the input lacks recognizable product details or is empty.
        GeminiRateLimitError: When rate limit (429) persists after all retries.
        GeminiAPIError: When API key is missing or non-retryable Gemini API call fails.
    """
    cleaned_text = post_text.strip() if post_text else ""
    if not cleaned_text and image_data is None:
        raise IrrelevantPostError("Post text and image data are both empty.")

    gemini_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        logger.error("GEMINI_API_KEY is not configured.")
        raise GeminiAPIError("GEMINI_API_KEY is not set in environment or settings.")

    # Prepare multimodal contents
    contents: List[Any] = []

    if image_data is not None:
        raw_bytes: bytes
        if isinstance(image_data, Image.Image):
            buffer = io.BytesIO()
            fmt = image_data.format or "JPEG"
            image_data.save(buffer, format=fmt)
            raw_bytes = buffer.getvalue()
            mime_type = f"image/{fmt.lower()}"
        else:
            raw_bytes = image_data

        image_part = types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)
        contents.append(image_part)

        if cleaned_text:
            contents.append(
                f"User text/notes: {cleaned_text}\n"
                "Analyze the provided image (and text if any) to identify the specific physical retail item, its brand, and model. "
                "Translate this into a precise Japanese search query for e-commerce platforms like Buyee/Mercari."
            )
        else:
            contents.append(
                "Analyze the provided image to identify the specific physical retail item, its brand, model, and item type. "
                "Translate this into a precise Japanese search query for e-commerce platforms like Buyee/Mercari."
            )
    else:
        contents.append(cleaned_text)

    client = genai.Client(api_key=gemini_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ParsedItem,
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.1,
    )
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )

            if not response or not response.text:
                raise GeminiAPIError("Empty response received from Gemini API.")

            # Parse structured output
            raw_json = json.loads(response.text)
            parsed_result = ParsedItem.model_validate(raw_json)

            # Validate that the item is relevant and has minimal query information
            if not parsed_result.is_anime_merch or not parsed_result.search_query_ja.strip():
                logger.info(f"Post/Image deemed irrelevant or lacking product info: {cleaned_text[:50]}...")
                raise IrrelevantPostError(
                    "無法解析此貼文，請確認是否包含明確的商品名稱或型號。"
                )

            logger.info(
                f"Successfully parsed input. Brand/Franchise: '{parsed_result.franchise}', "
                f"Model/Character: '{parsed_result.character}', Query: '{parsed_result.search_query_ja}', "
                f"Price: {parsed_result.fb_price_twd} TWD"
            )
            return parsed_result

        except IrrelevantPostError:
            raise
        except APIError as exc:
            is_rate_limit = (
                getattr(exc, "code", None) == 429
                or "429" in str(exc)
                or "RESOURCE_EXHAUSTED" in str(exc)
            )
            if is_rate_limit:
                if attempt < max_retries:
                    wait_time = retry_delay_seconds * attempt
                    logger.warning(
                        f"Gemini API rate limited (429/RESOURCE_EXHAUSTED). Retrying attempt {attempt}/{max_retries} in {wait_time}s..."
                    )
                    print(f"⏳ [Gemini 429 Rate Limit] Retrying attempt {attempt}/{max_retries} in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Gemini API rate limit exceeded after {max_retries} retries: {exc}")
                    raise GeminiRateLimitError("目前查詢人數較多，請稍後再試！") from exc
            else:
                logger.error(f"Gemini API returned an error: {exc}", exc_info=True)
                raise GeminiAPIError(f"Gemini API error: {exc}") from exc
        except json.JSONDecodeError as exc:
            logger.error(f"Failed to decode JSON from Gemini output: {exc}", exc_info=True)
            raise GeminiAPIError("Failed to parse Gemini response as JSON.") from exc
        except Exception as exc:
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                if attempt < max_retries:
                    wait_time = retry_delay_seconds * attempt
                    logger.warning(f"Rate limit exception caught. Retrying attempt {attempt}/{max_retries} in {wait_time}s...")
                    print(f"⏳ [Gemini 429 Rate Limit] Retrying attempt {attempt}/{max_retries} in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise GeminiRateLimitError("目前查詢人數較多，請稍後再試！") from exc
            logger.error(f"Unexpected error during Gemini parsing: {exc}", exc_info=True)
            raise GeminiAPIError(f"Unexpected parsing error: {exc}") from exc

    raise GeminiRateLimitError("目前查詢人數較多，請稍後再試！")
