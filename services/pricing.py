import logging
from typing import Optional

from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger("line_bot.pricing")


class PricingResult(BaseModel):
    """Pricing calculation breakdown and overpriced assessment."""

    price_jpy: float = Field(description="Base item price in Japanese Yen (JPY).")
    exchange_rate: float = Field(description="Applied JPY to TWD exchange rate.")
    proxy_fee_twd: float = Field(description="Proxy purchasing service fee in TWD.")
    shipping_twd: float = Field(description="Estimated international & domestic shipping in TWD.")
    landed_cost_twd: float = Field(description="Total estimated landed cost in TWD.")
    is_overpriced: bool = Field(description="True if FB resale price is > 30% above estimated landed cost.")
    fb_price_twd: Optional[float] = Field(default=None, description="Original FB post resale price in TWD if provided.")
    price_difference_twd: Optional[float] = Field(default=None, description="Difference between FB price and landed cost.")


def calculate_landed_cost(
    price_jpy: float,
    fb_price_twd: Optional[float] = None,
    exchange_rate: Optional[float] = None,
    proxy_fee_twd: Optional[float] = None,
    shipping_twd: Optional[float] = None,
) -> PricingResult:
    """
    Calculate the total estimated landed cost in TWD and assess if the FB resale price is overpriced.

    Formula:
        landed_cost_twd = (price_jpy * exchange_rate) + proxy_fee_twd + shipping_twd
        is_overpriced = fb_price_twd > (landed_cost_twd * 1.3)

    Args:
        price_jpy: Japanese listing price in JPY.
        fb_price_twd: Optional resale price extracted from FB post in TWD.
        exchange_rate: Optional custom exchange rate (defaults to settings).
        proxy_fee_twd: Optional custom proxy fee (defaults to settings).
        shipping_twd: Optional custom shipping cost (defaults to settings).

    Returns:
        PricingResult: Full calculation breakdown.
    """
    rate = exchange_rate if exchange_rate is not None else settings.default_exchange_rate_jpy_twd
    fee = proxy_fee_twd if proxy_fee_twd is not None else float(settings.default_proxy_fee_twd)
    shipping = shipping_twd if shipping_twd is not None else float(settings.default_estimated_shipping_twd)

    # Compute landed cost (rounded to nearest integer for clean presentation, represented as float)
    raw_landed_cost = (price_jpy * rate) + fee + shipping
    landed_cost_twd = round(raw_landed_cost, 2)

    is_overpriced = False
    price_diff: Optional[float] = None

    if fb_price_twd is not None and fb_price_twd > 0:
        # Overpriced threshold: > +30% over landed cost
        overpriced_threshold = landed_cost_twd * 1.3
        is_overpriced = fb_price_twd > overpriced_threshold
        price_diff = round(fb_price_twd - landed_cost_twd, 2)

    logger.info(
        f"Pricing calculated: JPY={price_jpy}, Landed Cost TWD={landed_cost_twd}, "
        f"FB Price TWD={fb_price_twd}, Overpriced={is_overpriced}"
    )

    return PricingResult(
        price_jpy=price_jpy,
        exchange_rate=rate,
        proxy_fee_twd=fee,
        shipping_twd=shipping,
        landed_cost_twd=landed_cost_twd,
        is_overpriced=is_overpriced,
        fb_price_twd=fb_price_twd,
        price_difference_twd=price_diff,
    )
