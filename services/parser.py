import asyncio
import io
import json
import logging
import os
import re
from typing import Any, List, Optional, Union

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError
from PIL import Image
from pydantic import BaseModel, Field, model_validator
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from services.scraper import normalize_search_keyword

logger = logging.getLogger("line_bot.parser")

DEFAULT_FALLBACK_MODEL = "gemini-flash-latest"


class ParsedItem(BaseModel):
    """Structured entity extraction schema for physical retail goods trading posts."""

    reasoning: Optional[str] = Field(
        default=None,
        description="Brief step-by-step explanation of the slang/abbreviation, product identity, or entity reasoning before generating final keywords.",
    )
    zh_keyword: Optional[str] = Field(
        default=None,
        description="The concise and precise Traditional Chinese search query for Taiwan and cross-border Chinese marketplaces.",
    )
    jp_keyword: Optional[str] = Field(
        default=None,
        description="The combined concise and precise Japanese search query for Japanese marketplaces (Mercari / Yahoo Auctions via Buyee).",
    )
    franchise: str = Field(
        default="",
        description="The core brand, manufacturer, or IP/series name (e.g., 'Sony', 'Yonex', 'Nikon', 'ハイキュー!!', 'Pokemon').",
    )
    character: str = Field(
        default="",
        description="The model name, character name, or specific product designation (e.g., 'WH-1000XM5', 'ASTROX 88D PRO', 'D850', '影山飛雄', 'リザードン').",
    )
    item_type: str = Field(
        default="",
        description="The product category or item type in standard Japanese or Chinese (e.g., 'ヘッドホン', 'バドミントンラケット', '一眼レフカメラ', '缶バッジ', 'フィギュア', 'スニーカー').",
    )
    year_or_edition: Optional[str] = Field(
        default=None,
        description="Special edition, version, generation, or release year if mentioned (e.g., 'Mark II', '2024', 'PRO', '限定版').",
    )
    keyword_jp: str = Field(
        default="",
        description="The combined concise and precise Japanese search query for Japanese marketplaces (Mercari / Yahoo Auctions via Buyee).",
    )
    keyword_zh: str = Field(
        default="",
        description="The concise and precise Traditional Chinese search query for Taiwan and cross-border Chinese marketplaces (Shopee Taiwan and Taobao).",
    )
    search_query_ja: str = Field(
        default="",
        description="Alias/backward-compatible field for keyword_jp.",
    )
    fb_price_twd: Optional[int] = Field(
        default=None,
        description="The extracted selling price in TWD (integer) from the Facebook post, or null if not found.",
    )
    is_anime_merch: bool = Field(
        default=True,
        description="True if the post describes any physical tradeable retail goods; False if irrelevant, spam, general text, or lacks identifiable product info.",
    )

    @model_validator(mode="after")
    def sync_keywords(self) -> "ParsedItem":
        # Synchronize jp_keyword / keyword_jp / search_query_ja
        if not self.keyword_jp and self.jp_keyword:
            self.keyword_jp = self.jp_keyword
        if not self.keyword_jp and self.search_query_ja:
            self.keyword_jp = self.search_query_ja
        if not self.search_query_ja and self.keyword_jp:
            self.search_query_ja = self.keyword_jp
        if not self.jp_keyword and self.keyword_jp:
            self.jp_keyword = self.keyword_jp

        # Synchronize zh_keyword / keyword_zh
        if not self.keyword_zh and self.zh_keyword:
            self.keyword_zh = self.zh_keyword
        if not self.keyword_zh:
            self.keyword_zh = f"{self.franchise} {self.character}".strip() or self.keyword_jp or self.search_query_ja
        if not self.zh_keyword and self.keyword_zh:
            self.zh_keyword = self.keyword_zh
        return self


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


class GeminiServerError(GeminiAPIError):
    """Raised when Gemini API server error (503 UNAVAILABLE / high demand) persists after retries."""
    pass


def is_404_error(exc: BaseException) -> bool:
    """Check if the exception corresponds to 404 NOT_FOUND."""
    if getattr(exc, "code", None) == 404:
        return True
    err_str = str(exc).lower()
    return "404" in err_str or "not found" in err_str


def is_503_or_server_error(exc: BaseException) -> bool:
    """Check if the exception corresponds to 503 UNAVAILABLE / 5xx ServerError or high demand."""
    if isinstance(exc, ServerError):
        return True
    status_code = getattr(exc, "code", None)
    if status_code in (500, 502, 503, 504):
        return True
    err_str = str(exc).lower()
    return (
        "503" in err_str
        or "unavailable" in err_str
        or "high demand" in err_str
        or "server error" in err_str
        or "service unavailable" in err_str
        or "temporarily overloaded" in err_str
    )


def is_rate_limit_error(exc: BaseException) -> bool:
    """Check if the exception corresponds to 429 RESOURCE_EXHAUSTED / rate limit."""
    if isinstance(exc, ClientError) and getattr(exc, "code", None) == 429:
        return True
    status_code = getattr(exc, "code", None)
    if status_code == 429:
        return True
    err_str = str(exc).lower()
    return (
        "429" in err_str
        or "resource_exhausted" in err_str
        or "rate limit" in err_str
        or "quota exceeded" in err_str
    )


def is_transient_error(exc: BaseException) -> bool:
    """Check if the error is transient and eligible for automatic exponential backoff retry."""
    return is_503_or_server_error(exc) or is_rate_limit_error(exc)


def resolve_model_name(raw_model: Optional[str] = None) -> str:
    """
    Normalize model string and substitute deprecated/missing models with active aliases.
    Strips 'models/' prefix if present and maps legacy names (e.g., gemini-1.5-flash) to gemini-flash-latest.
    """
    if not raw_model or not raw_model.strip():
        return DEFAULT_FALLBACK_MODEL

    clean = raw_model.replace("models/", "").strip()
    deprecated_models = {
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
        "gemini-1.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-pro",
        "gemini-1.0-pro",
    }
    if clean.lower() in deprecated_models:
        return DEFAULT_FALLBACK_MODEL
    return clean


def compress_and_resize_image(
    image_input: Union[bytes, Image.Image],
    max_dimension: int = 800,
    quality: int = 85,
) -> tuple[bytes, str]:
    """
    Resize image to a maximum bounding box (e.g. 800x800) preserving aspect ratio
    and compress it to JPEG bytes to drastically reduce network payload and latency.

    Returns:
        tuple[bytes, str]: (compressed_jpeg_bytes, mime_type)
    """
    if isinstance(image_input, bytes):
        img = Image.open(io.BytesIO(image_input))
    else:
        img = image_input

    # Convert transparent/palette images to RGB for clean JPEG compression
    if img.mode in ("RGBA", "P", "LA"):
        rgb_img = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "RGBA":
            rgb_img.paste(img, mask=img.split()[3])
        else:
            rgb_img.paste(img.convert("RGB"))
        img = rgb_img
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize proportionally if width or height exceeds max_dimension
    if img.width > max_dimension or img.height > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    compressed_bytes = buffer.getvalue()
    logger.info(
        f"Optimized image payload: {len(compressed_bytes)} bytes ({img.width}x{img.height}px)."
    )
    return compressed_bytes, "image/jpeg"


# Custom Dictionary for Common Colloquialisms, Taiwanese Slang, and Shorthand -> Official Japanese Listing Term
CUSTOM_KEYWORDS = {
    "蝴蝶王": "ビスカリア",
    "金標": "ビスカリア ゴールデン",
    "張繼科": "張継科",
    "林昀儒": "林イン儒",
    "樊振東": "樊振東",
    "小香": "シャネル",
    "大香": "シャネル",
    "水鬼": "サブマリーナー",
    "綠水鬼": "サブマリーナー グリーン",
    "黑水鬼": "サブマリーナー ブラック",
    "可樂圈": "GMTマスターII",
    "皮卡丘": "ピカチュウ",
    "噴火龍": "リザードン",
    "五條悟": "五条悟",
    "五條": "五条悟",
    "虎杖": "虎杖悠仁",
    "伏黑": "伏黒恵",
    "宿儺": "両面宿儺",
    "排少": "ハイキュー!!",
    "日向": "日向翔陽",
    "影山": "影山飛雄",
    "研磨": "孤爪研磨",
    "月島": "月島蛍",
    "及川": "及川徹",
    "咒術": "呪術廻戦",
    "王國之淚": "ゼルダの伝説 ティアーズ オブ ザ キングダム",
    "曠野之息": "ゼルダの伝説 ブレス オブ ザ ワイルド",
    "re:0": "Re:ゼロから始める異世界生活",
    "re0": "Re:ゼロから始める異世界生活",
    "從零開始": "Re:ゼロから始める異世界生活",
    "botw": "ゼルダの伝説 ブレス オブ ザ ワイルド",
    "totk": "ゼルダの伝説 ティアーズ オブ ザ キングダム",
}


SYSTEM_INSTRUCTION = """
You are an expert cross-border e-commerce translator. Think step-by-step about what the user's input actually means in pop culture or hobbyist circles before translating.

EXAMPLES:
User: 're:0'
Output: {"reasoning": "'re:0' is the popular shorthand for the anime 'Re:Zero - Starting Life in Another World'.", "zh_keyword": "RE:從零開始的異世界生活", "jp_keyword": "RE:ゼロから始める異世界生活"}

User: '蝴蝶王'
Output: {"reasoning": "Taiwanese table tennis slang for the Butterfly Viscaria blade.", "zh_keyword": "蝴蝶王", "jp_keyword": "ビスカリア"}

User: '五條'
Output: {"reasoning": "Refers to Satoru Gojo from the anime Jujutsu Kaisen.", "zh_keyword": "咒術迴戰 五條悟", "jp_keyword": "呪術廻戦 五条悟"}

Your primary objective is to act as a precision translator and query perfecter for cross-border shopping. When you receive a search query:
1. COLLOQUIAL TERMS: First, check if the user is using a Taiwanese colloquial product name or slang (e.g., '蝴蝶王', '小香', '金標', '水鬼'). If so, translate it to the OFFICIAL Japanese product name (e.g., 'ビスカリア', 'シャネル', 'ビスカリア ゴールデン', 'サブマリーナー') for Japanese platforms.
2. ABBREVIATIONS & SHORTHAND: Identify if the query is an abbreviation or shorthand for a well-known product, anime, game, or brand (e.g., 're:0' for 'Re:從零開始的異世界生活', 'botw' for '薩爾達傳說 曠野之息', 'totk' for '薩爾達傳說 王國之淚', '排少' for '排球少年!!'). You MUST automatically complete these abbreviations to their OFFICIAL and FULL titles in both Japanese (e.g., 'Re:ゼロから始める異世界生活') and Chinese (e.g., 'Re:從零開始的異世界生活').
3. OUTPUT: Always provide the perfected FULL, OFFICIAL product title for the 'Identified Product' field, and the translated official Japanese keywords for the 'Japanese Keywords' field, ensuring that searching with these results on their respective platforms will yield the most accurate and abundant results.

### STRICT RULES FOR KEYWORD GENERATION (`keyword_jp` & `keyword_zh`):
1. **Simplicity & Official Names**:
   - Provide the complete, official title used by native sellers on e-commerce platforms.
2. **No Filler Words**:
   - NEVER add qualifiers, generic categorizations, or redundant parent company words (e.g., do NOT add "本體", "機", "主機", "equipment", "device" unless part of the official model name).
3. **Preserve Global Brands & English Tech Terms**:
   - Keep internationally standard brand names and model numbers (e.g., 'Switch 2', 'Sony WH-1000XM5', 'Viscaria', 'Nikon Zfc', 'Air Jordan 1') in standard form.
4. **User Intent Match**:
   - If the user query is already a precise official title (e.g., 'Switch 2'), preserve it accurately.

### Crucial Fallback Rule for Unknown / Vague Products:
- If the exact brand, model, series, or character cannot be clearly identified from the user's image or text, NEVER fail, NEVER return empty values, and NEVER set `is_anime_merch: false`.
- Instead, deduce a general category keyword based on visual context and cues (e.g., '桌球拍' / '卓球ラケット', '底片相機' / 'フィルムカメラ', '羽球鞋' / 'バドミントンシューズ', '耳機' / 'ヘッドホン', '動漫公仔' / 'フィギュア', '相機' / 'カメラ', '球鞋' / 'スニーカー').
- Output the deduced category in BOTH `keyword_jp` (Japanese keyword for Buyee) and `keyword_zh` (Traditional Chinese keyword for Shopee and Taobao).
- Set `character` and `franchise` to the deduced category name if brand is unknown.
- Always set `is_anime_merch: true`.

### Multimodal Analysis Instructions:
- Analyze the provided image (and text if any) to identify the specific physical retail item or its general category.
- Generate two optimized search queries:
  1. `keyword_jp`: Concise, official Japanese search query for Japanese marketplaces (Mercari / Yahoo Auctions via Buyee).
  2. `keyword_zh`: Concise, official Traditional Chinese search query for Taiwan and cross-border Chinese marketplaces (Shopee Taiwan and Taobao).
- If an image is provided, inspect logos, packaging text, labels, model numbers, barcodes, character visual traits, colorways, or device physical form factors.

### Guidelines & Domain Knowledge:
1. **Trading Slang & Noise (MUST IGNORE when forming search queries)**:
   - Transaction actions: 售 (sell), 收 (buy/WTT), 換 (trade), 降價 (price drop), 誠可議 (negotiable), 出清 (clearance), 回血 (fund recovery), 退坑.
   - Condition & accessories: 全新 (brand new), 95成新 (like new), 9成新, 二手 (used), 附發票 (with receipt), 盒裝完整 (complete in box), 原廠配件 (original accessories), 默認初傷, 微瑕.
   - Bundling & logistics: 綁 (bundle), 不拆 (no split), 拆售, 雙北面交 (meetup), 賣貨便, 運費另計.
   - NEVER include these transaction/condition terms in `keyword_jp` or `keyword_zh`.

2. **Entity Extraction Rules**:
   - `reasoning`: Brief step-by-step CoT reasoning explaining the product/abbreviation.
   - `franchise`: Official Brand, Manufacturer, or IP Franchise Name (e.g., 'Re:ゼロから始める異世界生活', 'Sony', '任天堂', 'ハイキュー!!', 'Pokemon').
   - `character`: Model Name, Specific Product Name, Character, or Sub-line (e.g., 'エミリア', 'レム', 'WH-1000XM5', 'Switch 2', 'ビスカリア', '影山飛雄', 'リザードン').
   - `item_type`: Product Category in standard Japanese/Chinese (e.g., ヘッドホン, バドミントンラケット, ミラーレス一眼カメラ, スニーカー, 缶バッジ, フィギュア, トレカ).
   - `year_or_edition`: Generation, version, or year if it is critical for distinguishing the product (e.g., Mark II, Gen 2, 2024).

3. **Search Query Construction**:
   - `keyword_jp` / `jp_keyword`: Core perfected official Japanese product identifier (e.g., 'Re:ゼロから始める異世界生活', 'Switch 2', 'Sony WH-1000XM5', 'ビスカリア', '卓球ラケット').
   - `keyword_zh` / `zh_keyword`: Core perfected official Traditional Chinese product identifier (e.g., 'Re:從零開始的異世界生活', 'Switch 2', 'Sony WH-1000XM5', '蝴蝶王', '桌球拍').
   - Keep global brand names (e.g., Sony, Yonex, Canon, Apple, Nike, Switch) in standard Latin form.

4. **Price Extraction (`fb_price_twd`)**:
   - Extract the target item's selling price as an integer in TWD (e.g., '1500', '$1500', '1500元', 'NT$1500' -> 1500).
   - If no price is mentioned or it is purely an image/inquiry without price, set to null.

5. **Relevance Flag (`is_anime_merch` / is_valid_goods)**:
   - Always set `is_anime_merch: true` so a search comparison card is always produced for user browsing.
""".strip()


def fast_regex_parse(text: str) -> Optional[ParsedItem]:
    """
    Ultra-low latency (< 0.1ms) regex entity extraction for standard clean product queries.
    Bypasses LLM overhead for direct queries (e.g., 'Switch 2', 'PS5', 'Nikon Zfc', 'CCD 相機', '底片相機', 'Viscaria 桌球拍', '蝴蝶王').
    """
    if not text:
        return None

    clean = text.strip()
    if not clean or len(clean) > 40:
        return None

    # Check Custom Colloquialism & Shorthand Dictionary match first (case-insensitive)
    clean_lower = clean.lower()
    for custom_k, custom_v in CUSTOM_KEYWORDS.items():
        if clean_lower == custom_k.lower():
            return ParsedItem(
                franchise=custom_v,
                character="",
                item_type="商品",
                keyword_jp=custom_v,
                keyword_zh=clean,
                search_query_ja=custom_v,
                fb_price_twd=None,
                is_anime_merch=True,
            )

    # Skip fast-path if text contains trading verbs, conditions, or conversational tokens
    trading_and_chat_pattern = r"(?:^[\[【]?(?:售|出|買|賣|徵|求|換|問|推|換|出清)[\]】]?)|(?:推薦|請問|多少|好用|二手|九成新|成新|面交|郵寄|綁|私訊|放行|保固|正版|代理)"
    if re.search(trading_and_chat_pattern, clean):
        return None

    # Skip if contains sentence punctuation or newlines
    if re.search(r"[,，。！？!?\n\r:：【】\[\]()（）/／]", clean):
        return None

    norm_kw = normalize_search_keyword(clean)
    if len(norm_kw) >= 2:
        # Check if mapped in CUSTOM_KEYWORDS
        jp_term = CUSTOM_KEYWORDS.get(norm_kw.lower(), CUSTOM_KEYWORDS.get(norm_kw, norm_kw))
        return ParsedItem(
            franchise=norm_kw,
            character="",
            item_type="商品",
            keyword_jp=jp_term,
            keyword_zh=norm_kw,
            search_query_ja=jp_term,
            fb_price_twd=None,
            is_anime_merch=True,
        )

    return None


async def parse_fb_post(
    post_text: Optional[str] = None,
    image_data: Optional[Union[bytes, Image.Image]] = None,
    mime_type: str = "image/jpeg",
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay_seconds: float = 2.0,
) -> ParsedItem:
    """
    Extract structured retail item entities from text or images.
    Attempts ultra-fast Regex parsing first to bypass LLM latency (< 0.1ms).
    Falls back to Gemini Flash multimodal extraction for complex/multimodal posts.

    Args:
        post_text: Optional text or caption from user/post.
        image_data: Optional raw bytes or PIL Image object of the product image.
        mime_type: MIME type of the image if bytes (default "image/jpeg").
        api_key: Optional Gemini API key (defaults to settings/environment).
        max_retries: Maximum number of retry attempts for transient errors (default 3).
        retry_delay_seconds: Initial retry delay for exponential backoff (default 2.0s).

    Returns:
        ParsedItem: Structured entity extraction result.

    Raises:
        IrrelevantPostError: When the input lacks recognizable product details or is empty.
        GeminiServerError: When 503 UNAVAILABLE / server overload persists after retries.
        GeminiRateLimitError: When rate limit (429) persists after all retries.
        GeminiAPIError: When API key is missing or non-retryable Gemini API call fails.
    """
    cleaned_text = post_text.strip() if post_text else ""
    if not cleaned_text and image_data is None:
        raise IrrelevantPostError("Post text and image data are both empty.")

    # --- Fast-Path Regex Parsing (Bypass LLM latency for standard queries) ---
    if image_data is None and cleaned_text:
        fast_result = fast_regex_parse(cleaned_text)
        if fast_result is not None:
            logger.info(f"⚡ [Regex Fast-Path] Bypassed LLM for query: '{cleaned_text}' -> '{fast_result.search_query_ja}'")
            return fast_result

    gemini_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        logger.error("GEMINI_API_KEY is not configured.")
        raise GeminiAPIError("GEMINI_API_KEY is not set in environment or settings.")

    # Prepare multimodal contents
    contents: List[Any] = []

    if image_data is not None:
        compressed_bytes, resolved_mime = compress_and_resize_image(image_data)
        image_part = types.Part.from_bytes(data=compressed_bytes, mime_type=resolved_mime)
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
    model_name = resolve_model_name(os.getenv("GEMINI_MODEL", DEFAULT_FALLBACK_MODEL))
    fallback_applied = False

    def _should_retry(exc: BaseException) -> bool:
        nonlocal fallback_applied, model_name
        if isinstance(exc, IrrelevantPostError):
            return False
        if is_404_error(exc) and model_name != DEFAULT_FALLBACK_MODEL and not fallback_applied:
            fallback_applied = True
            logger.warning(
                f"Configured model '{model_name}' returned 404 NOT_FOUND. Automatically switching to '{DEFAULT_FALLBACK_MODEL}'..."
            )
            print(f"🔄 [Gemini Model Fallback] Switching from '{model_name}' to '{DEFAULT_FALLBACK_MODEL}'...")
            model_name = DEFAULT_FALLBACK_MODEL
            return True
        return is_transient_error(exc)

    attempt_count = 0
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_retries),
            wait=wait_exponential(
                multiplier=retry_delay_seconds,
                min=retry_delay_seconds,
                max=max(retry_delay_seconds * 4, retry_delay_seconds),
            ),
            retry=retry_if_exception(_should_retry),
            reraise=True,
        ):
            with attempt:
                attempt_count += 1
                try:
                    chat = client.aio.chats.create(model=model_name, config=config)
                    message_payload = contents if len(contents) > 1 else contents[0]
                    response = await chat.send_message(message=message_payload)

                    if not response or not response.text:
                        raise GeminiAPIError("Empty response received from Gemini API.")

                    # Parse structured output
                    raw_json = json.loads(response.text)
                    parsed_result = ParsedItem.model_validate(raw_json)

                    # Normalize keywords (spacing, full-width space removal, uppercase ASCII)
                    clean_jp = normalize_search_keyword(parsed_result.keyword_jp or parsed_result.search_query_ja)
                    clean_zh = normalize_search_keyword(parsed_result.keyword_zh or f"{parsed_result.franchise} {parsed_result.character}")

                    # Fallback deduction if model failed to provide keywords
                    if not clean_zh and not clean_jp:
                        if parsed_result.item_type:
                            clean_zh = normalize_search_keyword(parsed_result.item_type)
                            clean_jp = clean_zh
                        elif cleaned_text:
                            clean_zh = normalize_search_keyword(cleaned_text[:30])
                            clean_jp = clean_zh
                        else:
                            clean_zh = "熱門精選商品"
                            clean_jp = "人気商品"
                    elif not clean_zh:
                        clean_zh = clean_jp
                    elif not clean_jp:
                        clean_jp = clean_zh

                    parsed_result.keyword_jp = clean_jp
                    parsed_result.search_query_ja = clean_jp
                    parsed_result.keyword_zh = clean_zh
                    parsed_result.is_anime_merch = True

                    logger.info(
                        f"Successfully parsed input with model '{model_name}'. Brand/Franchise: '{parsed_result.franchise}', "
                        f"Model/Character: '{parsed_result.character}', JP Query: '{parsed_result.keyword_jp}', "
                        f"ZH Query: '{parsed_result.keyword_zh}', Price: {parsed_result.fb_price_twd} TWD"
                    )
                    return parsed_result

                except APIError as exc:
                    if is_transient_error(exc):
                        logger.warning(
                            f"Gemini API transient error ({getattr(exc, 'code', exc)}). Attempt {attempt_count}/{max_retries}..."
                        )
                        print(f"⏳ [Gemini Retry] Transient error encountered ({exc}). Retrying attempt {attempt_count}/{max_retries}...")
                        raise
                    if not is_404_error(exc):
                        logger.error(f"Gemini API returned an error: {exc}", exc_info=True)
                    raise
                except json.JSONDecodeError as exc:
                    logger.error(f"Failed to decode JSON from Gemini output: {exc}", exc_info=True)
                    raise GeminiAPIError("Failed to parse Gemini response as JSON.") from exc
                except Exception as exc:
                    if is_transient_error(exc):
                        logger.warning(f"Transient error caught. Attempt {attempt_count}/{max_retries}...")
                        print(f"⏳ [Gemini Retry] Transient error encountered ({exc}). Retrying attempt {attempt_count}/{max_retries}...")
                        raise
                    if not is_404_error(exc):
                        logger.error(f"Unexpected error during Gemini parsing: {exc}", exc_info=True)
                    raise

    except IrrelevantPostError:
        raise
    except Exception as exc:
        if is_503_or_server_error(exc):
            logger.error(f"Gemini API 503 ServerError / high demand persisted after {max_retries} attempts: {exc}")
            raise GeminiServerError("目前 AI 伺服器大塞車，請稍等一兩分鐘後再試一次喔！") from exc
        elif is_rate_limit_error(exc) or is_transient_error(exc):
            logger.error(f"Gemini API rate limit persisted after {max_retries} attempts: {exc}")
            raise GeminiRateLimitError("目前查詢人數較多，請稍後再試！") from exc
        logger.error(f"Gemini API error after retries: {exc}", exc_info=True)
        raise GeminiAPIError(f"Gemini API error: {exc}") from exc
