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


def remove_outliers(prices: List[float], trim_ratio: float = 0.2) -> List[float]:
    """
    Remove outliers by discarding the top (trim_ratio * 100)% highest
    and bottom (trim_ratio * 100)% lowest prices to filter out fake items,
    empty boxes, or scalpers. Default is 20% on each end.
    """
    if not prices:
        return []

    valid_prices = [float(p) for p in prices if p is not None and float(p) > 0]
    if not valid_prices:
        return []

    sorted_prices = sorted(valid_prices)
    n = len(sorted_prices)
    trim_count = int(n * trim_ratio)

    if trim_count <= 0 or (2 * trim_count >= n):
        return sorted_prices

    return sorted_prices[trim_count : n - trim_count]


def convert_to_twd(
    price: float,
    currency: str = "TWD",
    exchange_rate: Optional[float] = None,
    overseas_fee_rate: float = 0.015,
) -> float:
    """
    Convert price to TWD, incorporating a fixed 1.5% overseas transaction fee
    for foreign currencies (JPY, CNY). Domestic TWD incurs 0% fee.
    """
    if price is None:
        return 0.0

    curr = currency.upper().strip()
    if curr == "JPY":
        rate = exchange_rate if exchange_rate is not None else settings.default_exchange_rate_jpy_twd
        return price * rate * (1.0 + overseas_fee_rate)
    elif curr in ("CNY", "RMB"):
        rate = exchange_rate if exchange_rate is not None else getattr(settings, "default_exchange_rate_cny_twd", 4.5)
        return price * rate * (1.0 + overseas_fee_rate)
    elif curr == "USD":
        rate = exchange_rate if exchange_rate is not None else getattr(settings, "default_exchange_rate_usd_twd", 32.5)
        return price * rate * (1.0 + overseas_fee_rate)
    elif curr in ("TWD", "NTD"):
        return float(price)
    else:
        rate = exchange_rate if exchange_rate is not None else 1.0
        return price * rate * (1.0 + overseas_fee_rate)


class DynamicPriceResult(BaseModel):
    """Dynamic pricing summary across regional platforms with outlier removal and currency conversion."""

    min_price: Optional[int] = Field(default=None, description="Overall lowest cleaned price in TWD across platforms.")
    avg_price: Optional[int] = Field(default=None, description="Overall average cleaned price in TWD across platforms.")
    mercari_min_price: Optional[int] = Field(default=None, description="Mercari minimum price in TWD.")
    shopee_min_price: Optional[int] = Field(default=None, description="Shopee Taiwan minimum price in TWD.")
    taobao_min_price: Optional[int] = Field(default=None, description="Taobao minimum price in TWD.")
    yahoo_tw_min_price: Optional[int] = Field(default=None, description="Yahoo Taiwan minimum price in TWD.")
    yahoo_jp_min_price: Optional[int] = Field(default=None, description="Yahoo Auctions Japan minimum price in TWD.")
    rakuten_min_price: Optional[int] = Field(default=None, description="Rakuten Japan minimum price in TWD.")
    platform_min_prices: dict[str, Optional[int]] = Field(
        default_factory=dict,
        description="Dictionary mapping platform name to its minimum price in TWD.",
    )


DEFAULT_PLATFORM_CURRENCIES = {
    "mercari": "JPY",
    "buyee": "JPY",
    "yahoo_jp": "JPY",
    "yahoo_auctions": "JPY",
    "rakuten": "JPY",
    "taobao": "CNY",
    "shopee": "TWD",
    "yahoo_tw": "TWD",
}


def calculate_dynamic_platform_prices(
    platform_raw_prices: dict[str, List[float]],
    platform_currencies: Optional[dict[str, str]] = None,
    trim_ratio: float = 0.2,
    overseas_fee_rate: float = 0.015,
    jpy_rate: Optional[float] = None,
    cny_rate: Optional[float] = None,
) -> DynamicPriceResult:
    """
    Process raw search prices from each platform:
    1. Filter out fake items/empty boxes/scalpers with outlier removal (trim 20% top and bottom).
    2. Convert JPY and CNY to TWD, incorporating fixed 1.5% overseas transaction fee.
    3. Calculate overall min_price and avg_price in TWD.
    4. Calculate platform-specific minimum TWD prices (mercari_min_price, shopee_min_price, etc.).
    """
    currencies = dict(DEFAULT_PLATFORM_CURRENCIES)
    if platform_currencies:
        currencies.update({k.lower(): v for k, v in platform_currencies.items()})

    result = DynamicPriceResult()
    all_cleaned_twd: List[float] = []

    for platform_key, raw_prices in platform_raw_prices.items():
        clean_key = platform_key.lower().strip()
        curr = currencies.get(clean_key, "TWD")

        # Determine rate
        effective_rate = jpy_rate if curr == "JPY" else (cny_rate if curr in ("CNY", "RMB") else None)

        # 1. Outlier removal
        filtered_prices = remove_outliers(raw_prices, trim_ratio=trim_ratio)

        # 2. Currency conversion to TWD with 1.5% overseas fee
        twd_prices = [
            convert_to_twd(p, currency=curr, exchange_rate=effective_rate, overseas_fee_rate=overseas_fee_rate)
            for p in filtered_prices
        ]

        if twd_prices:
            plat_min = int(round(min(twd_prices)))
            result.platform_min_prices[clean_key] = plat_min
            all_cleaned_twd.extend(twd_prices)

            # Assign to specific attributes
            if "mercari" in clean_key or clean_key == "buyee":
                result.mercari_min_price = plat_min
            elif "shopee" in clean_key:
                result.shopee_min_price = plat_min
            elif "taobao" in clean_key:
                result.taobao_min_price = plat_min
            elif "yahoo_tw" in clean_key:
                result.yahoo_tw_min_price = plat_min
            elif "yahoo_jp" in clean_key or "yahoo_auctions" in clean_key:
                result.yahoo_jp_min_price = plat_min
            elif "rakuten" in clean_key:
                result.rakuten_min_price = plat_min
        else:
            result.platform_min_prices[clean_key] = None

    if all_cleaned_twd:
        result.min_price = int(round(min(all_cleaned_twd)))
        result.avg_price = int(round(sum(all_cleaned_twd) / len(all_cleaned_twd)))

    return result

