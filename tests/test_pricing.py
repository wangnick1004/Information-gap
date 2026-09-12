import pytest

from main import settings
from services.pricing import PricingResult, calculate_landed_cost


def test_calculate_landed_cost_standard():
    """Test standard landed cost calculation with default settings."""
    # JPY 1000 * 0.21 + 50 (proxy) + 150 (shipping) = 410.0 TWD
    result = calculate_landed_cost(
        price_jpy=1000.0,
        fb_price_twd=None,
        exchange_rate=0.21,
        proxy_fee_twd=50.0,
        shipping_twd=150.0,
    )

    assert isinstance(result, PricingResult)
    assert result.price_jpy == 1000.0
    assert result.exchange_rate == 0.21
    assert result.proxy_fee_twd == 50.0
    assert result.shipping_twd == 150.0
    assert result.landed_cost_twd == 410.0
    assert result.is_overpriced is False
    assert result.fb_price_twd is None
    assert result.price_difference_twd is None


def test_overpriced_threshold_true():
    """Test that FB price > 30% above landed cost triggers is_overpriced = True."""
    # Landed cost = 1000 * 0.21 + 50 + 150 = 410.0 TWD
    # Threshold = 410.0 * 1.3 = 533.0 TWD
    # FB Price = 600.0 TWD (> 533.0) -> Overpriced!
    result = calculate_landed_cost(
        price_jpy=1000.0,
        fb_price_twd=600.0,
        exchange_rate=0.21,
        proxy_fee_twd=50.0,
        shipping_twd=150.0,
    )

    assert result.landed_cost_twd == 410.0
    assert result.is_overpriced is True
    assert result.price_difference_twd == 190.0


def test_overpriced_threshold_false():
    """Test that fair FB price (<= 30% markup) returns is_overpriced = False."""
    # Landed cost = 410.0 TWD. Threshold = 533.0 TWD.
    # FB Price = 500.0 TWD (<= 533.0) -> Fair price
    result = calculate_landed_cost(
        price_jpy=1000.0,
        fb_price_twd=500.0,
        exchange_rate=0.21,
        proxy_fee_twd=50.0,
        shipping_twd=150.0,
    )

    assert result.landed_cost_twd == 410.0
    assert result.is_overpriced is False
    assert result.price_difference_twd == 90.0


def test_calculate_landed_cost_uses_settings():
    """Test fallback to global settings when parameters are omitted."""
    result = calculate_landed_cost(price_jpy=2000.0)
    expected_cost = (
        2000.0 * settings.default_exchange_rate_jpy_twd
        + float(settings.default_proxy_fee_twd)
        + float(settings.default_estimated_shipping_twd)
    )
    assert result.landed_cost_twd == round(expected_cost, 2)


def test_remove_outliers_top_bottom_20_percent():
    """Test that outlier removal discards the top 20% highest and bottom 20% lowest prices."""
    from services.pricing import remove_outliers

    # 15 prices: int(15 * 0.2) = 3 items trimmed from bottom, 3 from top.
    # 3 lowest: 100, 150, 200 (fake items/empty boxes)
    # 3 highest: 8000, 9000, 10000 (scalpers)
    raw_15 = [
        100.0, 150.0, 200.0,
        1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0, 1600.0, 1700.0, 1800.0,
        8000.0, 9000.0, 10000.0,
    ]
    cleaned = remove_outliers(raw_15, trim_ratio=0.2)
    assert len(cleaned) == 9
    assert cleaned == [
        1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0, 1600.0, 1700.0, 1800.0
    ]

    # Small list (< 5 items) retains all items
    small = [500.0, 600.0, 700.0]
    assert remove_outliers(small) == [500.0, 600.0, 700.0]

    # Empty and invalid items handled safely
    assert remove_outliers([]) == []
    assert remove_outliers([0.0, -10.0, None]) == []


def test_convert_to_twd_incorporates_overseas_fee():
    """Test that JPY and CNY conversions incorporate the fixed 1.5% overseas transaction fee."""
    from services.pricing import convert_to_twd

    # JPY: 10,000 JPY * 0.21 * (1 + 0.015) = 2,131.5 TWD
    jpy_twd = convert_to_twd(10000.0, currency="JPY", exchange_rate=0.21, overseas_fee_rate=0.015)
    assert pytest.approx(jpy_twd, 0.01) == 2131.5

    # CNY: 100 CNY * 4.5 * (1 + 0.015) = 456.75 TWD
    cny_twd = convert_to_twd(100.0, currency="CNY", exchange_rate=4.5, overseas_fee_rate=0.015)
    assert pytest.approx(cny_twd, 0.01) == 456.75

    # Domestic TWD: 1,000 TWD incurs 0% overseas fee
    twd_val = convert_to_twd(1000.0, currency="TWD")
    assert twd_val == 1000.0


def test_calculate_dynamic_platform_prices_overall_and_per_platform():
    """Test calculation of overall min_price, avg_price and platform-specific minimums."""
    from services.pricing import calculate_dynamic_platform_prices

    # Mercari prices (JPY, rate 0.21, fee 1.5% -> multiplier 0.21315)
    # 5 items: trim 1 bottom (1000) and 1 top (10000) -> kept: 2000, 3000, 4000
    # JPY 2000 * 0.21315 = 426.3 -> ~426 TWD
    mercari_raw = [1000.0, 2000.0, 3000.0, 4000.0, 10000.0]

    # Shopee prices (TWD, 0% fee)
    # 5 items: trim 1 bottom (100) and 1 top (2000) -> kept: 500, 600, 700
    shopee_raw = [100.0, 500.0, 600.0, 700.0, 2000.0]

    # Taobao prices (CNY, rate 4.5, fee 1.5% -> multiplier 4.5675)
    # 5 items: trim 1 bottom (20) and 1 top (500) -> kept: 100, 150, 200
    # CNY 100 * 4.5675 = 456.75 -> ~457 TWD
    taobao_raw = [20.0, 100.0, 150.0, 200.0, 500.0]

    platform_prices = {
        "mercari": mercari_raw,
        "shopee": shopee_raw,
        "taobao": taobao_raw,
        "yahoo_tw": [],  # empty platform
    }

    result = calculate_dynamic_platform_prices(
        platform_raw_prices=platform_prices,
        jpy_rate=0.21,
        cny_rate=4.5,
        trim_ratio=0.2,
        overseas_fee_rate=0.015,
    )

    # Mercari min: ~426 TWD
    assert result.mercari_min_price == 426
    # Shopee min: 500 TWD
    assert result.shopee_min_price == 500
    # Taobao min: ~457 TWD
    assert result.taobao_min_price == 457
    # Empty platform defaults to None
    assert result.yahoo_tw_min_price is None

    # Overall min_price should be min across all kept TWD prices (426)
    assert result.min_price == 426
    # avg_price should be average of all kept TWD prices
    assert result.avg_price is not None
    assert result.avg_price > result.min_price

