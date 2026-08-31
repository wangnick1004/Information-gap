import pytest
from linebot.v3.messaging import FlexContainer

from services.flex_builder import (
    append_affiliate_id,
    build_buyee_rakuten_search_url,
    build_buyee_yahoo_search_url,
    build_keyword_flex_message,
    build_price_comparison_flex,
    build_shopee_search_url,
    build_taobao_search_url,
    build_yahoo_tw_search_url,
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


def test_append_affiliate_id_with_base_url_redirect():
    """Test that AFFILIATE_BASE_URL wraps the Buyee URL in ?t={URL-encoded target}."""
    url = "https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5"
    base_url = "https://affiliate.example.com/click"
    affiliate_id = "test_aff_123"

    redirect_url = append_affiliate_id(url, affiliate_id=affiliate_id, affiliate_base_url=base_url)
    assert redirect_url.startswith("https://affiliate.example.com/click?t=")
    # Target URL should be URL-encoded, so :// and query params are encoded
    assert "https%3A%2F%2Fbuyee.jp" in redirect_url
    assert "af%3Dtest_aff_123" in redirect_url
    assert "keyword%3DSony%2520WH-1000XM5" in redirect_url or "keyword%3DSony" in redirect_url


def test_build_buyee_yahoo_search_url():
    """Test Yahoo! Japan Auctions search URL construction via Buyee."""
    url = build_buyee_yahoo_search_url("Sony WH-1000XM5 ヘッドホン")
    assert url.startswith("https://buyee.jp/item/search/query/")
    assert "Sony" in url or "SONY" in url
    assert "%E3%83%98%E3%83%83%E3%83%89%E3%83%9B%E3%83%B3" in url


def test_build_buyee_yahoo_search_url_with_affiliate_base_url():
    """Test Yahoo! Japan Auctions search URL wrapped with AFFILIATE_BASE_URL redirect tracking."""
    base_url = "https://affiliate.example.com/click"
    affiliate_id = "test_aff_123"
    url = build_buyee_yahoo_search_url("Sony WH-1000XM5", affiliate_id=affiliate_id, affiliate_base_url=base_url)
    assert url.startswith("https://affiliate.example.com/click?t=")
    assert "https%3A%2F%2Fbuyee.jp%2Fitem%2Fsearch%2Fquery" in url
    assert "af%3Dtest_aff_123" in url


def test_build_buyee_rakuten_search_url():
    """Test Rakuten Japan search URL construction via Buyee."""
    url = build_buyee_rakuten_search_url("Sony WH-1000XM5 ヘッドホン")
    assert url.startswith("https://buyee.jp/rakuten/shopping/search/category/0?query=")
    assert "Sony" in url or "SONY" in url
    assert "%E3%83%98%E3%83%83%E3%83%89%E3%83%9B%E3%83%B3" in url

    # Test alphanumeric space removal: Switch 2 -> SWITCH2, PS 5 -> PS5
    url_switch = build_buyee_rakuten_search_url("Switch 2")
    assert url_switch == "https://buyee.jp/rakuten/shopping/search/category/0?query=SWITCH2"

    url_ps5 = build_buyee_rakuten_search_url("PS 5 Pro")
    assert url_ps5 == "https://buyee.jp/rakuten/shopping/search/category/0?query=PS5%20PRO"


def test_build_buyee_rakuten_search_url_with_affiliate_base_url():
    """Test Rakuten Japan search URL wrapped with AFFILIATE_BASE_URL redirect tracking."""
    base_url = "https://affiliate.example.com/click"
    affiliate_id = "test_aff_123"
    url = build_buyee_rakuten_search_url("Sony WH-1000XM5", affiliate_id=affiliate_id, affiliate_base_url=base_url)
    assert url.startswith("https://affiliate.example.com/click?t=")
    assert "https%3A%2F%2Fbuyee.jp%2Frakuten%2Fshopping%2Fsearch%2Fcategory%2F0%3Fquery%3D" in url
    assert "af%3Dtest_aff_123" in url


def test_build_shopee_search_url():
    """Test Shopee Taiwan search URL construction with URL encoding."""
    url = build_shopee_search_url("Sony WH-1000XM5 耳機")
    assert url.startswith("https://shopee.tw/search?keyword=")
    assert "Sony" in url or "SONY" in url
    assert "%E8%80%B3%E6%A9%9F" in url  # '耳機' url-encoded


def test_build_shopee_search_url_with_affiliate_base_url():
    """Test Shopee Taiwan search URL wrapped with SHOPEE_AFFILIATE_BASE_URL redirect tracking."""
    shopee_base = "https://affiliate.shopee.tw/redirect"
    url = build_shopee_search_url("Sony WH-1000XM5 耳機", shopee_affiliate_base_url=shopee_base)
    assert url.startswith("https://affiliate.shopee.tw/redirect?t=")
    # Target URL should be fully URL-encoded
    assert "https%3A%2F%2Fshopee.tw%2Fsearch%3Fkeyword%3D" in url


def test_build_taobao_search_url():
    """Test Taobao search URL construction with URL encoding."""
    url = build_taobao_search_url("排球少年 影山飛雄 趴娃 2020")
    assert url.startswith("https://ai.taobao.com/search/index.htm?key=")
    assert "%E6%8E%92%E7%90%83%E5%B0%91%E5%B9%B4" in url  # '排球少年' url-encoded


def test_build_taobao_search_url_with_affiliate_base_url():
    """Test Taobao search URL wrapped with TAOBAO_AFFILIATE_BASE_URL redirect tracking."""
    taobao_base = "https://affiliate.taobao.com/redirect"
    url = build_taobao_search_url("排球少年 影山飛雄 趴娃 2020", taobao_affiliate_base_url=taobao_base)
    assert url.startswith("https://affiliate.taobao.com/redirect?t=")
    # Target URL should be fully URL-encoded
    assert "https%3A%2F%2Fai.taobao.com%2Fsearch%2Findex.htm%3Fkey%3D" in url


def test_build_yahoo_tw_search_url():
    """Test Yahoo Taiwan search URL construction with URL encoding."""
    url = build_yahoo_tw_search_url("排球少年 影山飛雄 趴娃 2020")
    assert url.startswith("https://tw.buy.yahoo.com/search/product?p=")
    assert "%E6%8E%92%E7%90%83%E5%B0%91%E5%B9%B4" in url


def test_build_yahoo_tw_search_url_with_affiliate_base_url():
    """Test Yahoo Taiwan search URL wrapped with YAHOO_TW_AFFILIATE_BASE_URL redirect tracking."""
    yahoo_tw_base = "https://affiliate.yahoo.com.tw/redirect"
    url = build_yahoo_tw_search_url("排球少年 影山飛雄 趴娃 2020", yahoo_tw_affiliate_base_url=yahoo_tw_base)
    assert url.startswith("https://affiliate.yahoo.com.tw/redirect?t=")
    assert "https%3A%2F%2Ftw.buy.yahoo.com%2Fsearch%2Fproduct%3Fp%3D" in url


def test_build_keyword_flex_message():
    """Test dedicated keyword Flex Message structure with carousel layout (Japan & Greater China cards)."""
    japanese_keyword = "Sony WH-1000XM5 ヘッドホン"
    search_url = "https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5%20%E3%83%98%E3%83%83%E3%83%89%E3%83%97%E3%83%B3"
    affiliate_id = "aff_test_888"

    flex_dict = build_keyword_flex_message(
        japanese_keyword=japanese_keyword,
        search_url=search_url,
        affiliate_id=affiliate_id,
        item_title="Sony WH-1000XM5",
        keyword_zh="Sony WH-1000XM5 耳機",
    )

    assert flex_dict["type"] == "carousel"
    assert len(flex_dict["contents"]) == 2

    # Card 1: Japan Focus
    card1 = flex_dict["contents"][0]
    assert card1["header"]["contents"][0]["text"] == "🇯🇵 日本精選平台"
    card1_body = [c.get("text", "") for c in card1["body"]["contents"]]
    assert any("搜尋關鍵字 (日文)：" in t for t in card1_body)
    assert any("SONY WH-1000XM5 ヘッドホン" in t for t in card1_body)

    card1_buttons = [c for c in card1["footer"]["contents"] if c.get("type") == "button"]
    assert len(card1_buttons) == 3
    assert card1_buttons[0]["action"]["label"] == "前往 Mercari (直購)"
    assert card1_buttons[0]["color"] == "#06C755"
    assert "af=aff_test_888" in card1_buttons[0]["action"]["uri"]

    assert card1_buttons[1]["action"]["label"] == "前往 日本雅虎 (競標)"
    assert card1_buttons[1]["color"] == "#6F42C1"
    assert "buyee.jp/item/search/query/" in card1_buttons[1]["action"]["uri"]

    assert card1_buttons[2]["action"]["label"] == "前往 日本樂天 (全新品)"
    assert card1_buttons[2]["color"] == "#BF0000"
    assert "buyee.jp/rakuten/shopping/search/category/0?query=" in card1_buttons[2]["action"]["uri"]

    # Card 2: Greater China Focus
    card2 = flex_dict["contents"][1]
    assert card2["header"]["contents"][0]["text"] == "🇹🇼/🇨🇳 綜合網購平台"
    card2_body = [c.get("text", "") for c in card2["body"]["contents"]]
    assert any("搜尋關鍵字 (中文)：" in t for t in card2_body)

    card2_buttons = [c for c in card2["footer"]["contents"] if c.get("type") == "button"]
    assert len(card2_buttons) == 3
    assert card2_buttons[0]["action"]["label"] == "前往 台灣蝦皮"
    assert card2_buttons[0]["color"] == "#EE4D2D"
    assert "shopee.tw/search?keyword=" in card2_buttons[0]["action"]["uri"]

    assert card2_buttons[1]["action"]["label"] == "前往 淘寶"
    assert card2_buttons[1]["color"] == "#FF5000"
    assert "ai.taobao.com/search/index.htm?key=" in card2_buttons[1]["action"]["uri"]

    assert card2_buttons[2]["action"]["label"] == "前往 台灣 Yahoo"
    assert card2_buttons[2]["color"] == "#6001D2"
    assert "tw.buy.yahoo.com/search/product?p=" in card2_buttons[2]["action"]["uri"]

    # Verify line-bot-sdk parsing
    container = FlexContainer.from_dict(flex_dict)
    assert container is not None


def test_build_price_comparison_flex_overpriced():
    """Test Flex Bubble generation when item is overpriced with 3 marketplace buttons."""
    parsed = ParsedAnimeItem(
        franchise="ハイキュー!!",
        character="影山飛雄",
        item_type="もちもちマスコット",
        year_or_edition="2020",
        keyword_jp="ハイキュー 影山 もちもちマスコット 2020",
        keyword_zh="排球少年 影山飛雄 趴娃 2020",
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

    assert flex_dict["type"] == "carousel"
    assert len(flex_dict["contents"]) == 2

    # Card 1: Japan Focus
    card1 = flex_dict["contents"][0]
    assert card1["hero"]["url"] == "https://static.mercdn.net/item/detail/orig/photos/m1.jpg"
    assert card1["header"]["contents"][0]["text"] == "🇯🇵 日本精選平台"

    card1_buttons = [c for c in card1["footer"]["contents"] if c.get("type") == "button"]
    assert len(card1_buttons) == 3
    assert card1_buttons[0]["action"]["label"] == "前往 Mercari (直購)"
    assert card1_buttons[0]["color"] == "#06C755"
    assert "af=my_affiliate_tag" in card1_buttons[0]["action"]["uri"]

    assert card1_buttons[1]["action"]["label"] == "前往 日本雅虎 (競標)"
    assert card1_buttons[1]["color"] == "#6F42C1"
    assert "buyee.jp/item/search/query/" in card1_buttons[1]["action"]["uri"]

    assert card1_buttons[2]["action"]["label"] == "前往 日本樂天 (全新品)"
    assert card1_buttons[2]["color"] == "#BF0000"
    assert "buyee.jp/rakuten/shopping/search/category/0?query=" in card1_buttons[2]["action"]["uri"]

    # Card 2: Greater China Focus
    card2 = flex_dict["contents"][1]
    assert card2["header"]["contents"][0]["text"] == "🇹🇼/🇨🇳 綜合網購平台"

    card2_buttons = [c for c in card2["footer"]["contents"] if c.get("type") == "button"]
    assert len(card2_buttons) == 3
    assert card2_buttons[0]["action"]["label"] == "前往 台灣蝦皮"
    assert card2_buttons[0]["color"] == "#EE4D2D"
    assert "shopee.tw/search?keyword=" in card2_buttons[0]["action"]["uri"]

    assert card2_buttons[1]["action"]["label"] == "前往 淘寶"
    assert card2_buttons[1]["color"] == "#FF5000"
    assert "ai.taobao.com/search/index.htm?key=" in card2_buttons[1]["action"]["uri"]

    assert card2_buttons[2]["action"]["label"] == "前往 台灣 Yahoo"
    assert card2_buttons[2]["color"] == "#6001D2"
    assert "tw.buy.yahoo.com/search/product?p=" in card2_buttons[2]["action"]["uri"]

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
        query="呪術廻戰 五条悟 缶バッジ",
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

    assert flex_dict["type"] == "carousel"
    assert len(flex_dict["contents"]) == 2
    assert flex_dict["contents"][0]["hero"]["url"] == "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=600&auto=format&fit=crop&q=80"
    assert flex_dict["contents"][0]["footer"]["contents"][0]["action"]["label"] == "前往 Mercari (直購)"
    assert flex_dict["contents"][0]["footer"]["contents"][1]["action"]["label"] == "前往 日本雅虎 (競標)"
    assert flex_dict["contents"][0]["footer"]["contents"][2]["action"]["label"] == "前往 日本樂天 (全新品)"
    assert flex_dict["contents"][1]["footer"]["contents"][0]["action"]["label"] == "前往 台灣蝦皮"
    assert flex_dict["contents"][1]["footer"]["contents"][1]["action"]["label"] == "前往 淘寶"
    assert flex_dict["contents"][1]["footer"]["contents"][2]["action"]["label"] == "前往 台灣 Yahoo"

    container = FlexContainer.from_dict(flex_dict)
    assert container is not None


def test_build_price_comparison_flex_with_affiliate_base_url():
    """Test Flex Bubble URL format when AFFILIATE_BASE_URL is provided."""
    parsed = ParsedAnimeItem(
        franchise="Sony",
        character="WH-1000XM5",
        item_type="ヘッドホン",
        search_query_ja="Sony WH-1000XM5 ヘッドホン",
        fb_price_twd=8000,
        is_anime_merch=True,
    )
    pricing = PricingResult(
        price_jpy=30000.0,
        exchange_rate=0.21,
        proxy_fee_twd=50.0,
        shipping_twd=150.0,
        landed_cost_twd=6500.0,
        is_overpriced=True,
        fb_price_twd=8000.0,
        price_difference_twd=1500.0,
    )
    scraper = ScrapingResult(
        query="Sony WH-1000XM5 ヘッドホン",
        search_url="https://buyee.jp/mercari/search?keyword=Sony%20WH-1000XM5",
        lowest_price_jpy=28000.0,
        median_price_jpy=30000.0,
        representative_image_url="https://static.mercdn.net/item/detail/orig/photos/m1.jpg",
        sample_prices=[28000.0, 30000.0],
        total_found=2,
    )

    flex_dict = build_price_comparison_flex(
        parsed_item=parsed,
        pricing_result=pricing,
        scraper_result=scraper,
        affiliate_id="aff_id_999",
        affiliate_base_url="https://track.buyee-affiliate.com/redirect",
        shopee_affiliate_base_url="https://track.shopee-affiliate.com/redirect",
        taobao_affiliate_base_url="https://track.taobao-affiliate.com/redirect",
        yahoo_tw_affiliate_base_url="https://track.yahoo-tw-affiliate.com/redirect",
    )

    assert flex_dict["type"] == "carousel"
    assert len(flex_dict["contents"]) == 2

    # Card 1: Japan Focus
    card1 = flex_dict["contents"][0]
    card1_buttons = [c for c in card1["footer"]["contents"] if c.get("type") == "button"]
    assert len(card1_buttons) == 3

    btn_uri = card1_buttons[0]["action"]["uri"]
    yahoo_btn_uri = card1_buttons[1]["action"]["uri"]
    rakuten_btn_uri = card1_buttons[2]["action"]["uri"]
    hero_uri = card1["hero"]["action"]["uri"]

    assert btn_uri.startswith("https://track.buyee-affiliate.com/redirect?t=")
    assert hero_uri.startswith("https://track.buyee-affiliate.com/redirect?t=")
    assert "https%3A%2F%2Fbuyee.jp%2Fmercari%2Fsearch" in btn_uri
    assert "af%3Daff_id_999" in btn_uri

    assert yahoo_btn_uri.startswith("https://track.buyee-affiliate.com/redirect?t=")
    assert "https%3A%2F%2Fbuyee.jp%2Fitem%2Fsearch%2Fquery" in yahoo_btn_uri
    assert "af%3Daff_id_999" in yahoo_btn_uri
    assert card1_buttons[1]["action"]["label"] == "前往 日本雅虎 (競標)"

    assert rakuten_btn_uri.startswith("https://track.buyee-affiliate.com/redirect?t=")
    assert "https%3A%2F%2Fbuyee.jp%2Frakuten%2Fshopping%2Fsearch%2Fcategory%2F0%3Fquery%3D" in rakuten_btn_uri
    assert "af%3Daff_id_999" in rakuten_btn_uri
    assert card1_buttons[2]["action"]["label"] == "前往 日本樂天 (全新品)"

    # Card 2: Greater China Focus
    card2 = flex_dict["contents"][1]
    card2_buttons = [c for c in card2["footer"]["contents"] if c.get("type") == "button"]
    assert len(card2_buttons) == 3

    shopee_btn_uri = card2_buttons[0]["action"]["uri"]
    taobao_btn_uri = card2_buttons[1]["action"]["uri"]
    yahoo_tw_btn_uri = card2_buttons[2]["action"]["uri"]

    assert shopee_btn_uri.startswith("https://track.shopee-affiliate.com/redirect?t=")
    assert "https%3A%2F%2Fshopee.tw%2Fsearch%3Fkeyword%3D" in shopee_btn_uri

    assert taobao_btn_uri.startswith("https://track.taobao-affiliate.com/redirect?t=")
    assert "https%3A%2F%2Fai.taobao.com%2Fsearch%2Findex.htm%3Fkey%3D" in taobao_btn_uri

    assert yahoo_tw_btn_uri.startswith("https://track.yahoo-tw-affiliate.com/redirect?t=")
    assert "https%3A%2F%2Ftw.buy.yahoo.com%2Fsearch%2Fproduct%3Fp%3D" in yahoo_tw_btn_uri
    assert card2_buttons[2]["action"]["label"] == "前往 台灣 Yahoo"

