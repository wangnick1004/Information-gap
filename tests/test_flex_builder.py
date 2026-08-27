import pytest
from linebot.v3.messaging import FlexContainer

from services.flex_builder import (
    append_affiliate_id,
    build_keyword_flex_message,
    build_price_comparison_flex,
)
from services.parser import ParsedAnimeItem
from services.pricing import PricingResult
from services.scraper import ScrapingResult


def test_append_affiliate_id():
    """Test that affiliate ID query param is appended correctly."""
    url = "https://buyee.jp/mercari/search?keyword=%E3%83%8F%E3%82%A4%E3%82%AD%E3%83%A5%E3%83%BC"
    affiliate_id = "test_aff_999"

    new_url = append_affiliate_id(url, affiliate_id)
    assert "af=test_aff_999" in new_url
    assert "keyword=" in new_url

    # No affiliate ID passed
    assert append_affiliate_id(url, None) == url
    assert append_affiliate_id(url, "") == url


def test_build_keyword_flex_message():
    """Test dedicated keyword Flex Message structure with green Buyee button."""
    japanese_keyword = "Sony WH-1000XM5 ヘッドホン"
    search_url = "https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5%20%E3%83%98%E3%83%83%E3%83%89%E3%83%97%E3%83%B3"
    affiliate_id = "aff_test_888"

    flex_dict = build_keyword_flex_message(
        japanese_keyword=japanese_keyword,
        search_url=search_url,
        affiliate_id=affiliate_id,
        item_title="Sony WH-1000XM5",
    )

    assert flex_dict["type"] == "bubble"

    # 1. Header verification
    assert "header" in flex_dict
    header_text = flex_dict["header"]["contents"][0]["text"]
    assert "比價成功，來去撈便宜～" in header_text
    assert flex_dict["header"]["contents"][0]["weight"] == "bold"

    # 2. Body verification
    assert "body" in flex_dict
    body_texts = [c.get("text", "") for c in flex_dict["body"]["contents"]]
    assert any("搜尋關鍵字：" in t for t in body_texts)
    assert any("Sony WH-1000XM5 ヘッドホン" in t for t in body_texts)

    # 3. Footer verification
    assert "footer" in flex_dict
    button = flex_dict["footer"]["contents"][0]
    assert button["type"] == "button"
    assert button["style"] == "primary"
    assert button["color"] == "#06C755"
    assert button["action"]["type"] == "uri"
    assert button["action"]["label"] == "前往 Buyee 尋寶"
    assert "af=aff_test_888" in button["action"]["uri"]

    # Verify line-bot-sdk parsing
    container = FlexContainer.from_dict(flex_dict)
    assert container is not None


def test_build_price_comparison_flex_overpriced():
    """Test Flex Bubble generation when item is overpriced."""
    parsed = ParsedAnimeItem(
        franchise="ハイキュー!!",
        character="影山飛雄",
        item_type="もちもちマスコット",
        year_or_edition="2020",
        search_query_ja="ハイキュー 影山 もちもちマスコット 2020",
        fb_price_twd=1500,
        is_anime_merch=True,
    )
    pricing = PricingResult(
        price_jpy=1500.0,
        exchange_rate=0.21,
        proxy_fee_twd=50.0,
        shipping_twd=150.0,
        landed_cost_twd=515.0,
        is_overpriced=True,
        fb_price_twd=1500.0,
        price_difference_twd=985.0,
    )
    scraper = ScrapingResult(
        query="ハイキュー 影山 もちもちマスコット 2020",
        search_url="https://buyee.jp/mercari/search?keyword=test",
        lowest_price_jpy=1200.0,
        median_price_jpy=1500.0,
        representative_image_url="https://static.mercdn.net/item/detail/orig/photos/m1.jpg",
        sample_prices=[1200.0, 1500.0, 1800.0],
        total_found=3,
    )

    flex_dict = build_price_comparison_flex(
        parsed_item=parsed,
        pricing_result=pricing,
        scraper_result=scraper,
        affiliate_id="my_affiliate_tag",
    )

    assert flex_dict["type"] == "bubble"
    assert flex_dict["hero"]["url"] == "https://static.mercdn.net/item/detail/orig/photos/m1.jpg"
    assert "af=my_affiliate_tag" in flex_dict["footer"]["contents"][0]["action"]["uri"]
    assert flex_dict["footer"]["contents"][0]["color"] == "#06C755"
    assert flex_dict["footer"]["contents"][0]["action"]["label"] == "前往 Buyee 尋寶"

    # Verify line-bot-sdk v3 FlexContainer can parse the generated structure cleanly
    container = FlexContainer.from_dict(flex_dict)
    assert container is not None


def test_build_price_comparison_flex_fair_price():
    """Test Flex Bubble generation when item is fair priced."""
    parsed = ParsedAnimeItem(
        franchise="呪術廻戰",
        character="五条悟",
        item_type="缶バッジ",
        search_query_ja="呪術廻戦 五条悟 缶バッジ",
        fb_price_twd=200,
        is_anime_merch=True,
    )
    pricing = PricingResult(
        price_jpy=500.0,
        exchange_rate=0.21,
        proxy_fee_twd=50.0,
        shipping_twd=150.0,
        landed_cost_twd=305.0,
        is_overpriced=False,
        fb_price_twd=200.0,
        price_difference_twd=-105.0,
    )
    scraper = ScrapingResult(
        query="呪術廻戦 五条悟 缶バッジ",
        search_url="https://buyee.jp/mercari/search?keyword=test",
        lowest_price_jpy=400.0,
        median_price_jpy=500.0,
        representative_image_url=None,  # Tests fallback placeholder image
        sample_prices=[400.0, 500.0],
        total_found=2,
    )

    flex_dict = build_price_comparison_flex(
        parsed_item=parsed,
        pricing_result=pricing,
        scraper_result=scraper,
        affiliate_id=None,
    )

    assert flex_dict["type"] == "bubble"
    assert flex_dict["hero"]["url"] == "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=600&auto=format&fit=crop&q=80"
    assert flex_dict["footer"]["contents"][0]["action"]["label"] == "前往 Buyee 尋寶"

    container = FlexContainer.from_dict(flex_dict)
    assert container is not None
