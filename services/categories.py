"""
商品類別與「類別 → 6 個平台」對照表。

這是唯一決定查哪些平台的地方：要調整某類別的平台，只改 CATEGORY_PLATFORMS。
平台名稱對應 services.platforms.build_platforms() 的鍵（有測試確保兩者一致，打錯名稱會失敗）。
目前卡片把日本平台放第一張、其餘放第二張，各自依此表順序；依價格排序屬工作票 06。
平台選擇不由 AI 決定，AI 只負責判斷類別。
"""

from enum import Enum
from typing import Mapping, Tuple


class Category(str, Enum):
    ELECTRONICS = "3C 家電"
    BEAUTY = "美妝保養"
    FASHION = "服飾鞋包"
    ANIME_TOYS = "動漫周邊/玩具"
    SPORTS = "運動戶外"
    OTHER = "其他"

    @classmethod
    def parse(cls, value: object) -> "Category":
        """把 AI 輸出的類別轉成 Category；無法辨識時為「其他」。"""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip())
        except ValueError:
            return cls.OTHER


# 平台名稱：pchome=PChome 24h、momo=momo 購物、shopee=蝦皮、yahoo_tw=Yahoo 購物、ruten=露天、
# rakuten=日本樂天、taobao=淘寶、mercari=Mercari、yahoo_jp=日本 Yahoo 拍賣
CATEGORY_PLATFORMS: Mapping[Category, Tuple[str, ...]] = {
    Category.ELECTRONICS: ("pchome", "momo", "shopee", "yahoo_tw", "ruten", "rakuten"),
    Category.BEAUTY: ("momo", "shopee", "pchome", "yahoo_tw", "rakuten", "taobao"),
    Category.FASHION: ("shopee", "momo", "taobao", "ruten", "mercari", "rakuten"),
    Category.ANIME_TOYS: ("mercari", "yahoo_jp", "rakuten", "shopee", "ruten", "taobao"),
    Category.SPORTS: ("momo", "pchome", "shopee", "yahoo_tw", "rakuten", "taobao"),
    Category.OTHER: ("shopee", "momo", "pchome", "yahoo_tw", "ruten", "taobao"),
}


def platforms_for(category: Category) -> Tuple[str, ...]:
    return CATEGORY_PLATFORMS.get(category, CATEGORY_PLATFORMS[Category.OTHER])
