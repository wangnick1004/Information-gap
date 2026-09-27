import asyncio
import io
import json
import logging
import os
import time
from typing import Any, Iterable, List, Optional, Union

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError
from PIL import Image
from pydantic import BaseModel, Field, field_validator, model_validator
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from config import settings
from services.categories import Category
from services.scraper import normalize_search_keyword

logger = logging.getLogger("line_bot.parser")

DEFAULT_FALLBACK_MODEL = "gemini-flash-latest"
# Gemini 3 系列預設會先「思考」數百個 token（實測 gemini-3.6-flash 約 400–900 個，單次 4–6 秒）；
# 抽取關鍵字不需要長推理，調成 low 後單次約 2 秒、品質相同。可用環境變數 GEMINI_THINKING_LEVEL 覆寫，
# 設為 default 則沿用模型預設。
DEFAULT_THINKING_LEVEL = "low"
# AI 忙碌、逾時時給使用者的訊息
AI_BUSY_MESSAGE = "目前 AI 伺服器大塞車，請稍等一兩分鐘後再試一次喔！"


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
    perfected_keyword: Optional[str] = Field(
        default=None,
        description="The optimal, fully corrected standard product search string produced by automatic autocorrection (e.g., converting 'switch' to 'Nintendo Switch', fixing typos, casing, or incomplete names).",
    )
    suggested_term: Optional[str] = Field(
        default=None,
        description="The optimal, fully qualified standard product name (e.g., 'Apple iPhone 15' instead of 'iphone') if the user's input is a typo, overly broad, or missing a brand name; null if input is already specific, accurate, and standard.",
    )
    fb_price_twd: Optional[int] = Field(
        default=None,
        description="The extracted selling price in TWD (integer) from the Facebook post, or null if not found.",
    )
    estimated_min_usd: Optional[int] = Field(
        default=None,
        description="根據你對該商品的知識，預估該『主商品（排除空盒與廉價配件）』在二手市場的合理『最低』美金價格，並填入 estimated_min_usd。",
    )
    category: Category = Field(
        default=Category.OTHER,
        description="商品類別，只能是以下之一：3C 家電、美妝保養、服飾鞋包、動漫周邊/玩具、運動戶外、其他。無法判斷時填「其他」。",
    )
    is_anime_merch: bool = Field(
        default=True,
        description="True if the post describes any physical tradeable retail goods; False if irrelevant, spam, general text, or lacks identifiable product info.",
    )

    @field_validator("category", mode="before")
    @classmethod
    def unknown_category_is_other(cls, value: object) -> Category:
        return Category.parse(value)

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

        # Synchronize perfected_keyword and suggested_term
        if not self.perfected_keyword:
            if self.suggested_term:
                self.perfected_keyword = self.suggested_term
            else:
                self.perfected_keyword = self.keyword_zh or f"{self.franchise} {self.character}".strip()
        return self


# Alias for backward compatibility across modules
ParsedAnimeItem = ParsedItem


class GeminiOutput(BaseModel):
    """
    交給 Gemini 的結構化輸出格式：中文與日文關鍵字各只有一個欄位且必填。
    ParsedItem 的重複欄位（keyword_jp、search_query_ja 等）若都設為選填，Gemini 有時整組不填，
    日本平台就只剩中文可查。
    """

    reasoning: str = Field(description="Brief step-by-step explanation of the product identity.")
    perfected_keyword: str = Field(description="Fully corrected standard product name.")
    zh_keyword: str = Field(description="Traditional Chinese search query for Taiwanese platforms and Taobao.")
    jp_keyword: str = Field(
        description="Authentic native Japanese search query for Japanese platforms. Never Traditional Chinese."
    )
    category: Category = Field(description="商品類別：3C 家電、美妝保養、服飾鞋包、動漫周邊/玩具、運動戶外、其他。")
    franchise: str = Field(default="", description="Brand, manufacturer, or IP/series name.")
    character: str = Field(default="", description="Model name, product name, or character.")
    item_type: str = Field(default="", description="Product type in Japanese (e.g., ヘッドホン, フィギュア).")
    year_or_edition: Optional[str] = Field(default=None, description="Generation, version, size, or year.")
    fb_price_twd: Optional[int] = Field(default=None, description="Seller's price in TWD, or null.")
    estimated_min_usd: Optional[int] = Field(default=None, description="預估主商品在市場上的合理最低美金價格。")


class JapaneseKeyword(BaseModel):
    """日文關鍵字補問的輸出格式。"""

    jp_keyword: str


_KANA_RANGES = ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F))


def _has_kana(text: str) -> bool:
    return any(lo <= ord(ch) <= hi for ch in text for lo, hi in _KANA_RANGES)


def _has_han(text: str) -> bool:
    return any(0x4E00 <= ord(ch) <= 0x9FFF or 0x3400 <= ord(ch) <= 0x4DBF for ch in text)


def is_japanese_keyword(keyword: str, chinese_keywords: Iterable[str] = ()) -> bool:
    """
    日本平台可用的關鍵字：含假名，或只有品牌／型號等英數字（如 Nintendo Switch 2）。
    只有漢字時無法與中文區分，只在與中文關鍵字（或使用者原文）不同時才接受（如「呪術廻戦 五条悟」）。
    """
    keyword = normalize_search_keyword(keyword)
    if not keyword:
        return False
    if _has_kana(keyword) or not _has_han(keyword):
        return True
    return keyword not in {normalize_search_keyword(text) for text in chinese_keywords if text}


JAPANESE_KEYWORD_PROMPT = (
    "Rewrite this product as a concise search query in authentic native Japanese for Japanese marketplaces "
    "(Mercari, Yahoo Auctions, Rakuten). Use the official Japanese product/series names, katakana for "
    "loanwords, and keep brand names and model numbers in Latin form. Never output Chinese.\n"
    "Product: {product}"
)


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
    return isinstance(exc, asyncio.TimeoutError) or is_503_or_server_error(exc) or is_rate_limit_error(exc)


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


def thinking_config_for(model_name: str) -> Optional[types.ThinkingConfig]:
    """
    Gemini 3 系列用 thinking_level 控制思考量；其他模型（含 *-latest 別名，實際版本不固定）
    不一定接受這個參數，維持模型預設以免 400 錯誤。
    """
    level = os.getenv("GEMINI_THINKING_LEVEL", DEFAULT_THINKING_LEVEL).strip().lower()
    if not level or level == "default" or not model_name.startswith("gemini-3"):
        return None
    return types.ThinkingConfig(thinking_level=level)


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


DEFAULT_VISION_PROMPT = (
    "你現在是一位頂級的網購商品鑑定專家。請分析這張圖片，並精準辨識出圖片中的『主體商品』。\n"
    "執行步驟：\n"
    "1. 放大檢視圖片中的任何文字、Logo、標籤或型號（啟動 OCR）。\n"
    "2. 忽略背景與人物，只專注於商品本身。\n"
    "3. 找出『品牌＋精確型號／商品名稱』；若是角色商品或玩具，找出『作品名稱＋角色＋商品種類』。\n"
    "4. 判斷商品類別（3C 家電、美妝保養、服飾鞋包、動漫周邊/玩具、運動戶外、其他）。\n"
    "5. 【絕對限制】：搜尋關鍵字欄位『只』放最精確的商品搜尋關鍵字（例如：'Fujifilm X100V' 或 'Nike Air Jordan 1 Low'），絕對不要輸出完整的句子或描述性廢話。"
)


SYSTEM_INSTRUCTION = """
You are an expert shopping assistant for Taiwanese online shoppers. Users send casual text (often slang,
abbreviations, or pasted marketplace posts with prices and noise) or a product photo. Think step-by-step
about which exact product the user means, then output search keywords for shopping platforms and the
product category.

EXAMPLES:
User: 'AJ1'
Output: {"reasoning": "'AJ1' is the common shorthand for the Nike Air Jordan 1 sneaker.", "perfected_keyword": "Nike Air Jordan 1", "zh_keyword": "Nike Air Jordan 1", "jp_keyword": "ナイキ エアジョーダン1", "category": "服飾鞋包", "estimated_min_usd": 90}

User: 'switch2'
Output: {"reasoning": "'switch2' means Nintendo's Switch 2 game console.", "perfected_keyword": "Nintendo Switch 2", "zh_keyword": "Nintendo Switch 2", "jp_keyword": "Nintendo Switch 2", "category": "3C 家電", "estimated_min_usd": 400}

User: '售 小棕瓶 50ml 全新 2500'
Output: {"reasoning": "'小棕瓶' is the nickname of Estée Lauder Advanced Night Repair serum; price 2500 TWD.", "perfected_keyword": "雅詩蘭黛 特潤超導全方位修護露 50ml", "zh_keyword": "雅詩蘭黛 小棕瓶 50ml", "jp_keyword": "エスティローダー アドバンス ナイト リペア 50ml", "category": "美妝保養", "fb_price_twd": 2500, "estimated_min_usd": 60}

User: '蝴蝶王'
Output: {"reasoning": "Taiwanese table tennis slang for the Butterfly Viscaria blade.", "perfected_keyword": "Butterfly Viscaria 桌球拍", "zh_keyword": "蝴蝶王", "jp_keyword": "ビスカリア", "category": "運動戶外", "estimated_min_usd": 80}

User: '排少 影山 趴娃'
Output: {"reasoning": "'排少' is the anime Haikyu!!, '影山' is Tobio Kageyama, '趴娃' is a lying-down plush mascot.", "perfected_keyword": "排球少年 影山飛雄 趴娃", "zh_keyword": "排球少年 影山飛雄 趴娃", "jp_keyword": "ハイキュー 影山飛雄 もちもちマスコット", "category": "動漫周邊/玩具", "estimated_min_usd": 15}

### 1. Understanding the product
- COLLOQUIAL TERMS: Translate Taiwanese nicknames and slang (e.g., '蝴蝶王', '小香', '小棕瓶', '水鬼') to the official product name.
- KEEP WHAT THE USER WROTE: model numbers, generations, and capacities the user explicitly wrote (e.g., 'pro 3', 'xm6', '230ml') must stay exactly as written, even if you do not recognize that product; newer products than your knowledge exist. Only fill in a generation when the user wrote none.
- ABBREVIATIONS & SHORTHAND: Expand abbreviations and model shorthand (e.g., 'AJ1' -> 'Nike Air Jordan 1', 'switch2' -> 'Nintendo Switch 2', 'ps5' -> 'PlayStation 5', 're0' -> 'Re:從零開始的異世界生活'). Never search with the raw abbreviation when a full official name exists.
- If the exact brand or model cannot be identified, NEVER fail and NEVER return empty keywords: deduce a general product keyword from the context (e.g., '藍牙耳機' / 'ワイヤレスイヤホン', '球鞋' / 'スニーカー', '桌球拍' / '卓球ラケット').

### 2. Keywords
- `perfected_keyword`: the optimal, fully corrected standard product name in Traditional Chinese or the official brand/model form (fix typos, casing, abbreviations, incomplete names).
- `zh_keyword`: concise Traditional Chinese search query for Taiwanese platforms (蝦皮, momo, PChome, Yahoo 購物, 露天) and Taobao.
- `jp_keyword`: ALWAYS required, even for Chinese input. Concise search query in authentic native Japanese for Japanese platforms (Rakuten, Mercari, Yahoo Auctions via Buyee): Japanese official series/product names and katakana for loanwords. NEVER copy the Chinese keyword or output Traditional Chinese in this field (e.g., '桌球拍' -> '卓球ラケット', '咒術迴戰' -> '呪術廻戦').
- Keep global brand names and model numbers in standard Latin form (e.g., Sony WH-1000XM5, Switch 2, Air Jordan 1).
- NEVER add filler words ("本體", "主機", "equipment", "device") unless part of the official name.
- IGNORE trading noise in keywords: transaction words (售, 收, 徵, 換, 降價, 誠可議, 出清, 回血), condition words (全新, 未拆, 95成新, 二手, 微瑕, 附發票, 盒裝完整), bundling/logistics (綁, 不拆, 面交, 賣貨便, 運費另計).

### 3. Entity fields
- `reasoning`: brief step-by-step explanation of the slang/abbreviation and product identity.
- `franchise`: brand, manufacturer, or IP/series (e.g., 'Sony', 'Nike', '任天堂', 'ハイキュー!!').
- `character`: model name, specific product name, or character (e.g., 'WH-1000XM5', 'Air Jordan 1', '影山飛雄').
- `item_type`: product type (e.g., ヘッドホン, スニーカー, 美容液, フィギュア).
- `year_or_edition`: generation, version, capacity/size, or year if it distinguishes the product.

### 4. Category (`category`)
Choose exactly one of: "3C 家電", "美妝保養", "服飾鞋包", "動漫周邊/玩具", "運動戶外", "其他".
- 3C 家電: phones, computers, game consoles, cameras, audio, home appliances.
- 美妝保養: cosmetics, skincare, fragrance, personal care.
- 服飾鞋包: clothing, shoes/sneakers, bags, accessories, watches.
- 動漫周邊/玩具: anime/manga/game character goods, figures, plush, trading cards, toys, models.
- 運動戶外: sports equipment, sportswear-specific gear, fitness, camping, outdoor.
- If unsure, use "其他".

### 5. Prices
- `fb_price_twd`: the seller's price in TWD as an integer if the text contains one (e.g., '1500', '$1500', '1500元', 'NT$1500' -> 1500); otherwise null.
- `estimated_min_usd`: 根據你對該商品的知識，預估該『主商品（排除空盒與廉價配件）』在市場上的合理『最低』美金價格。

### 6. Relevance
- Always set `is_anime_merch: true` (legacy field name meaning "is a product") so a comparison card is always produced.

### Images
- If an image is provided, inspect logos, packaging text, labels, model numbers, barcodes, and physical form to identify the main product; ignore background and people.
""".strip()


async def _japanese_keyword_fallback(
    client: Any,
    model_name: str,
    parsed: ParsedItem,
    keyword_zh: str,
    rejected_jp: str,
    chinese_keywords: Iterable[str],
    timeout_seconds: float,
) -> str:
    """
    AI 沒給出可用的日文關鍵字時：先單獨再問一次日文關鍵字，再試 item_type，
    都不行才退回中文關鍵字。每一步都留 warning log，不默默改用中文。
    """
    logger.warning(
        f"[Japanese Keyword] AI returned no usable Japanese keyword (got '{rejected_jp}') for '{keyword_zh}'; asking again"
    )
    product = parsed.perfected_keyword or keyword_zh
    try:
        chat = client.aio.chats.create(
            model=model_name,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JapaneseKeyword,
                temperature=0.1,
                thinking_config=thinking_config_for(model_name),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        response = await asyncio.wait_for(
            chat.send_message(message=JAPANESE_KEYWORD_PROMPT.format(product=product)), timeout=timeout_seconds
        )
        retried = normalize_search_keyword(JapaneseKeyword.model_validate_json(response.text).jp_keyword)
        if is_japanese_keyword(retried, chinese_keywords):
            logger.info(f"[Japanese Keyword] retry produced '{retried}'")
            return retried
        logger.warning(f"[Japanese Keyword] retry still not Japanese: '{retried}'")
    except Exception as exc:
        logger.warning(f"[Japanese Keyword] retry failed: {type(exc).__name__}: {exc}")

    item_type = normalize_search_keyword(parsed.item_type)
    if is_japanese_keyword(item_type, chinese_keywords):
        logger.warning(f"[Japanese Keyword] using item_type '{item_type}' for Japanese platforms")
        return item_type
    logger.warning(f"[Japanese Keyword] falling back to Chinese keyword '{keyword_zh}' for Japanese platforms")
    return keyword_zh


async def parse_fb_post(
    post_text: Optional[str] = None,
    image_data: Optional[Union[bytes, Image.Image]] = None,
    mime_type: str = "image/jpeg",
    api_key: Optional[str] = None,
    max_retries: int = 2,
    retry_delay_seconds: float = 0.5,
    vision_prompt: Optional[str] = None,
    attempt_timeout_seconds: float = 4.0,
    total_timeout_seconds: float = 7.0,
) -> ParsedItem:
    """
    Extract structured retail item entities (keywords + category) from text or images.
    Every input goes through Gemini so abbreviations and slang are expanded.

    每次呼叫 Gemini 最多等 attempt_timeout_seconds，逾時視同暫時性錯誤重試；
    所有嘗試與等待合計不超過 total_timeout_seconds：重試只拿到剩下的時間，用完就不再重試。
    預設的 total_timeout_seconds 等於比價流程給 AI 解析的時間（services.comparison.AI_PARSE_BUDGET_SECONDS），
    因此第一次嘗試可以比「均分兩次」更長（實測單次約 2 秒、偶有 3 秒以上），
    而 503/429 這類很快就失敗的錯誤仍有時間重試一次。
    """
    cleaned_text = post_text.strip().lower() if post_text else ""
    if not cleaned_text and image_data is None:
        raise IrrelevantPostError("Post text and image data are both empty.")

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

        effective_vision_prompt = vision_prompt or DEFAULT_VISION_PROMPT
        if cleaned_text:
            contents.append(
                f"User text/notes: {cleaned_text}\n{effective_vision_prompt}"
            )
        else:
            contents.append(effective_vision_prompt)
    else:
        contents.append(cleaned_text)

    client = genai.Client(api_key=gemini_key)

    def _config_for(model: str) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeminiOutput,
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.1,
            thinking_config=thinking_config_for(model),
            # 沒有傳入任何工具，AFC 本來就不會多打 API（實測關閉前後延遲相同）；關閉只是省掉每次的 AFC 日誌
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
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
    started = time.monotonic()
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_retries) | stop_after_delay(total_timeout_seconds),
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
                    remaining = total_timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    chat = client.aio.chats.create(model=model_name, config=_config_for(model_name))
                    message_payload = contents if len(contents) > 1 else contents[0]
                    response = await asyncio.wait_for(
                        chat.send_message(message=message_payload),
                        timeout=min(attempt_timeout_seconds, remaining),
                    )

                    if not response or not response.text:
                        raise GeminiAPIError("Empty response received from Gemini API.")

                    # Parse structured output
                    raw_json = json.loads(response.text)
                    parsed_result = ParsedItem.model_validate(raw_json)

                    # Normalize keywords (spacing, full-width space removal, uppercase ASCII)
                    clean_zh = normalize_search_keyword(
                        parsed_result.keyword_zh or f"{parsed_result.franchise} {parsed_result.character}"
                    ) or normalize_search_keyword(parsed_result.perfected_keyword or "")
                    if not clean_zh:
                        clean_zh = normalize_search_keyword(cleaned_text[:30]) or "熱門精選商品"

                    clean_jp = normalize_search_keyword(parsed_result.keyword_jp or parsed_result.search_query_ja)
                    chinese_keywords = (clean_zh, parsed_result.perfected_keyword, cleaned_text)
                    if not is_japanese_keyword(clean_jp, chinese_keywords):
                        clean_jp = await _japanese_keyword_fallback(
                            client, model_name, parsed_result, clean_zh, clean_jp, chinese_keywords,
                            # 補問也計入總時限，只拿剩下的時間
                            min(attempt_timeout_seconds, max(0.0, total_timeout_seconds - (time.monotonic() - started))),
                        )

                    parsed_result.keyword_jp = clean_jp
                    parsed_result.search_query_ja = clean_jp
                    parsed_result.keyword_zh = clean_zh
                    parsed_result.is_anime_merch = True

                    logger.info(
                        f"Successfully parsed input with model '{model_name}'. Brand/Franchise: '{parsed_result.franchise}', "
                        f"Model/Character: '{parsed_result.character}', JP Query: '{parsed_result.keyword_jp}', "
                        f"ZH Query: '{parsed_result.keyword_zh}', Category: {parsed_result.category.value}, "
                        f"Price: {parsed_result.fb_price_twd} TWD, "
                        f"Est Min USD: {parsed_result.estimated_min_usd}"
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
        if isinstance(exc, asyncio.TimeoutError):
            logger.error(
                f"Gemini API timed out after {attempt_count} attempts in {time.monotonic() - started:.1f}s "
                f"(up to {attempt_timeout_seconds}s per attempt, {total_timeout_seconds}s in total)"
            )
            raise GeminiServerError(AI_BUSY_MESSAGE) from exc
        if is_503_or_server_error(exc):
            logger.error(f"Gemini API 503 ServerError / high demand persisted after {max_retries} attempts: {exc}")
            raise GeminiServerError(AI_BUSY_MESSAGE) from exc
        elif is_rate_limit_error(exc) or is_transient_error(exc):
            logger.error(f"Gemini API rate limit persisted after {max_retries} attempts: {exc}")
            raise GeminiRateLimitError("目前查詢人數較多，請稍後再試！") from exc
        logger.error(f"Gemini API error after retries: {exc}", exc_info=True)
        raise GeminiAPIError(f"Gemini API error: {exc}") from exc
