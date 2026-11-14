"""楽天市場商品検索API クライアント."""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .config import Settings


@dataclass
class Item:
    item_code: str
    name: str
    price: int
    item_url: str
    affiliate_url: str
    image_url: str
    review_average: float
    review_count: int
    shop_name: str
    caption: str

    @classmethod
    def from_api(cls, raw: dict) -> "Item":
        images = raw.get("mediumImageUrls") or []
        image = images[0] if images else ""
        if isinstance(image, dict):  # formatVersion=1 互換
            image = image.get("imageUrl", "")
        # 128x128 のサムネイルを大きいサイズに差し替える
        image = image.replace("?_ex=128x128", "?_ex=600x600")
        return cls(
            item_code=raw["itemCode"],
            name=raw["itemName"],
            price=int(raw["itemPrice"]),
            item_url=raw["itemUrl"],
            affiliate_url=raw.get("affiliateUrl") or raw["itemUrl"],
            image_url=image,
            review_average=float(raw.get("reviewAverage") or 0),
            review_count=int(raw.get("reviewCount") or 0),
            shop_name=raw.get("shopName", ""),
            caption=(raw.get("itemCaption") or "")[:600],
        )


class RakutenClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        if not settings.rakuten_app_id:
            raise RuntimeError("RAKUTEN_APP_ID が設定されていません")
        if not settings.rakuten_affiliate_id:
            raise RuntimeError("RAKUTEN_AFFILIATE_ID が設定されていません（未設定だと報酬が発生しません）")
        self.settings = settings
        self.session = session or requests.Session()

    def search(self, keyword: str, hits: int = 30, sort: str = "-reviewCount") -> list[Item]:
        params = {
            "applicationId": self.settings.rakuten_app_id,
            "affiliateId": self.settings.rakuten_affiliate_id,
            "keyword": keyword,
            "hits": hits,
            "sort": sort,
            "imageFlag": 1,
            "availability": 1,
            "formatVersion": 2,
        }
        if self.settings.rakuten_access_key:
            params["accessKey"] = self.settings.rakuten_access_key

        for attempt in range(4):
            resp = self.session.get(self.settings.rakuten_search_url, params=params, timeout=20)
            if resp.status_code == 429:  # 1リクエスト/秒の制限
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return [Item.from_api(raw) for raw in resp.json().get("Items", [])]
        resp.raise_for_status()
        return []
