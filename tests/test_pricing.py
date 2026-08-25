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
