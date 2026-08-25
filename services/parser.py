import json
import logging
import os
from typing import Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger("line_bot.parser")


class ParsedAnimeItem(BaseModel):
    """Structured entity extraction schema for anime merchandise posts."""

    franchise: str = Field(
        default="",
        description="The anime/manga/game title translated to standard Japanese (e.g., 'ハイキュー!!', '呪術廻戦').",
    )
    character: str = Field(
        default="",
        description="The character name in standard Japanese kanji/katakana (e.g., '影山飛雄', '五条悟').",
    )
    item_type: str = Field(
        default="",
        description="The type of merchandise in Japanese (e.g., 'もちもちマスコット', '缶バッジ', 'アクリルスタンド', 'フィギュア').",
    )
    year_or_edition: Optional[str] = Field(
        default=None,
        description="Special edition, event, or release year if mentioned (e.g., '2020', 'ジャンプフェスタ', 'バースデー').",
    )
    search_query_ja: str = Field(
        default="",
        description="The combined optimal Japanese search string for Japanese marketplaces (Mercari / Yahoo Auctions via Buyee).",
    )
    fb_price_twd: Optional[int] = Field(
        default=None,
        description="The extracted selling price in TWD (integer) from the Facebook post, or null if not found.",
    )
    is_anime_merch: bool = Field(
        default=True,
        description="True if the post is an anime/manga/game merchandise trade/sale; False if irrelevant, general text, or missing merchandise details.",
    )


class ParsingError(Exception):
    """Base exception for parsing errors."""
    pass


class IrrelevantPostError(ParsingError):
    """Raised when the text lacks recognizable anime merchandise details or is irrelevant."""
    pass


class GeminiAPIError(ParsingError):
    """Raised when the Gemini API call fails or credentials are missing."""
    pass


SYSTEM_INSTRUCTION = """
You are an expert in Japanese ACG (Anime, Comic, Games) merchandise valuation and Taiwanese secondary market trading terminology.
Your mission is to parse noisy, slang-heavy Facebook trading posts (Traditional Chinese) and extract structured entity details translated into accurate Japanese marketplace search terms (Mercari / Buyee).

### Domain Knowledge & Slang Guide:
1. **Taiwanese Trading Slang (MUST IGNORE when forming search queries)**:
   - Transaction actions: 售 (sell), 收 (buy/WTT), 換 (trade), 退坑 (leaving fandom), 出清 (clearance), 回血 (fund recovery), 可議 (negotiable).
   - Bundling terms: 綁 (bundle, e.g., 綁1, 綁2, 需多帶), 不拆 (no split), 帶多優先 (priority for multiple items), 默認初傷 (default factory flaws), 微瑕 (minor defects).
   - NEVER include these transaction terms in `search_query_ja`.

2. **Merchandise Category Translation (Mandarin Slang -> Japanese)**:
   - 趴娃 / 趴趴 -> もちもちマスコット (or おまんじゅう / ぬいぐるみ)
   - 徽章 / 吧唧 / 胸章 -> 缶バッジ
   - 拍立得 / 相卡 -> ぱしゃこれ (or ポラショット / チェキ / スナップマイド)
   - 壓克力立牌 / 立牌 / 壓克力 -> アクリルスタンド
   - 生日磚 / 壓克力磚 -> アクリルブロック / バースデーブロック
   - 色紙 -> 色紙
   - 娃娃 / 玩偶 / 棉花娃 -> ぬいぐるみ / マスコット
   - 公仔 / 手辦 / 景品 -> フィギュア
   - 一番賞 -> 一番くじ
   - 特典 -> 特典

3. **Common Series Nicknames (Translate to official Japanese title)**:
   - 排少 / HQ / 排球 -> ハイキュー!!
   - 咒術 / 咒術迴戰 / JJK -> 呪術廻戦
   - 我推 / 推之子 -> 推しの子
   - 藍色監獄 / 藍監 -> ブルーロック
   - 獵人 / 全職獵人 -> HUNTER×HUNTER
   - 鬼滅 -> 鬼滅の刃
   - 合奏 / 偶像夢幻祭 / ES -> あんさんぶるスターズ!!
   - 名偵探柯南 / 柯南 -> 名探偵コナン
   - 吉伊卡哇 -> ちいかわ
   - 葬送的芙莉蓮 -> 葬送のフリーレン
   - 間諜家家酒 -> SPY×FAMILY
   - 文豪野犬 -> 文豪ストレイドッグス
   - 家教 / 家庭教師 -> 家庭教師ヒットマンREBORN!

4. **Search Query Construction (`search_query_ja`)**:
   - Combine: `[Franchise (JA)] [Character (JA)] [Item Type (JA)] [Edition/Year (if applicable)]` separated by spaces.
   - Keep it concise, high-relevance, and search-friendly for Japanese marketplace engines.

5. **Price Extraction (`fb_price_twd`)**:
   - Extract the target item's selling price as an integer in TWD (e.g., '1500', '$1500', '1500元' -> 1500).
   - If no price is mentioned or it is purely a trade/inquiry post without a price, set to null.

6. **Relevance Flag (`is_anime_merch`)**:
   - Set `is_anime_merch: true` if the post contains valid anime/manga merchandise trade info.
   - Set `is_anime_merch: false` if the input is general conversational text, greetings, irrelevant content, or does not mention identifiable anime merchandise.
""".strip()


async def parse_fb_post(
    post_text: str,
    api_key: Optional[str] = None,
) -> ParsedAnimeItem:
    """
    Parse a Facebook anime trading post using Gemini 1.5 Flash structured output.

    Args:
        post_text: Raw text or content extracted from a Facebook post.
        api_key: Optional Gemini API key (defaults to settings/environment).

    Returns:
        ParsedAnimeItem: Structured entity extraction result.

    Raises:
        IrrelevantPostError: When the post is not anime merchandise or lacks details.
        GeminiAPIError: When API key is missing or Gemini API call fails.
    """
    cleaned_text = post_text.strip() if post_text else ""
    if not cleaned_text:
        raise IrrelevantPostError("Post text is empty or blank.")

    gemini_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        logger.error("GEMINI_API_KEY is not configured.")
        raise GeminiAPIError("GEMINI_API_KEY is not set in environment or settings.")

    try:
        client = genai.Client(api_key=gemini_key)

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ParsedAnimeItem,
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.1,
        )

        response = await client.aio.models.generate_content(
            model="gemini-1.5-flash",
            contents=cleaned_text,
            config=config,
        )

        if not response or not response.text:
            raise GeminiAPIError("Empty response received from Gemini API.")

        # Parse structured output
        raw_json = json.loads(response.text)
        parsed_result = ParsedAnimeItem.model_validate(raw_json)

        # Validate that the item is relevant and has minimal query information
        if not parsed_result.is_anime_merch or not parsed_result.search_query_ja.strip():
            logger.info(f"Post deemed irrelevant or lacking merchandise info: {cleaned_text[:50]}...")
            raise IrrelevantPostError(
                "Post does not contain recognizable anime merchandise information."
            )

        logger.info(
            f"Successfully parsed post. Franchise: '{parsed_result.franchise}', "
            f"Character: '{parsed_result.character}', Query: '{parsed_result.search_query_ja}', "
            f"Price: {parsed_result.fb_price_twd} TWD"
        )
        return parsed_result

    except IrrelevantPostError:
        raise
    except APIError as exc:
        logger.error(f"Gemini API returned an error: {exc}", exc_info=True)
        raise GeminiAPIError(f"Gemini API error: {exc}") from exc
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to decode JSON from Gemini output: {exc}", exc_info=True)
        raise GeminiAPIError("Failed to parse Gemini response as JSON.") from exc
    except Exception as exc:
        logger.error(f"Unexpected error during Gemini parsing: {exc}", exc_info=True)
        raise GeminiAPIError(f"Unexpected parsing error: {exc}") from exc
