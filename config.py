from typing import Optional
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load environment variables from .env if present
load_dotenv()


class Settings(BaseSettings):
    """Application settings and environment configurations."""

    line_channel_secret: str = Field(default="", alias="LINE_CHANNEL_SECRET")
    line_channel_access_token: str = Field(default="", alias="LINE_CHANNEL_ACCESS_TOKEN")
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    buyee_affiliate_id: Optional[str] = Field(default=None, alias="BUYEE_AFFILIATE_ID")
    affiliate_base_url: Optional[str] = Field(default=None, alias="AFFILIATE_BASE_URL")
    shopee_affiliate_base_url: Optional[str] = Field(default=None, alias="SHOPEE_AFFILIATE_BASE_URL")
    taobao_affiliate_base_url: Optional[str] = Field(default=None, alias="TAOBAO_AFFILIATE_BASE_URL")
    yahoo_tw_affiliate_base_url: Optional[str] = Field(default=None, alias="YAHOO_TW_AFFILIATE_BASE_URL")
    default_exchange_rate_jpy_twd: float = Field(default=0.21, alias="DEFAULT_EXCHANGE_RATE_JPY_TWD")
    default_estimated_shipping_twd: int = Field(default=150, alias="DEFAULT_ESTIMATED_SHIPPING_TWD")
    default_proxy_fee_twd: int = Field(default=50, alias="DEFAULT_PROXY_FEE_TWD")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
