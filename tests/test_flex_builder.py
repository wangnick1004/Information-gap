import pytest
from linebot.v3.messaging import FlexContainer

from services.flex_builder import (
    append_affiliate_id,
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
    # Fallback image was assigned
    assert flex_dict["hero"]["url"].startswith("http")

    container = FlexContainer.from_dict(flex_dict)
    assert container is not None
