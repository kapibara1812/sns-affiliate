"""環境変数とファイルパスの設定."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENRES_FILE = ROOT / "config" / "genres.json"
POSTS_FILE = ROOT / "data" / "posts.json"
INSIGHTS_CSV = ROOT / "data" / "insights.csv"
DOCS_DIR = ROOT / "docs"
IMAGES_DIR = DOCS_DIR / "images"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # 楽天
    rakuten_app_id: str = field(default_factory=lambda: _env("RAKUTEN_APP_ID"))
    rakuten_access_key: str = field(default_factory=lambda: _env("RAKUTEN_ACCESS_KEY"))
    rakuten_affiliate_id: str = field(default_factory=lambda: _env("RAKUTEN_AFFILIATE_ID"))
    rakuten_search_url: str = field(
        default_factory=lambda: _env(
            "RAKUTEN_ITEM_SEARCH_URL",
            "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601",
        )
    )
    min_review_count: int = field(default_factory=lambda: int(_env("MIN_REVIEW_COUNT", "30")))
    min_review_average: float = field(default_factory=lambda: float(_env("MIN_REVIEW_AVERAGE", "4.0")))
    repost_cooldown_days: int = field(default_factory=lambda: int(_env("REPOST_COOLDOWN_DAYS", "90")))

    # まとめ投稿: 自動モードでまとめ投稿にする曜日（1=月 … 7=日）と紹介する商品数
    roundup_weekdays: str = field(default_factory=lambda: _env("ROUNDUP_WEEKDAYS", "3,6"))
    roundup_size: int = field(default_factory=lambda: int(_env("ROUNDUP_SIZE", "5")))

    # A8.net: 自動モードで A8 案件を投稿する曜日（1=月 … 7=日）と、同じ案件を再投稿するまでの日数
    a8_weekdays: str = field(default_factory=lambda: _env("A8_WEEKDAYS", "1,4"))
    a8_cooldown_days: int = field(default_factory=lambda: int(_env("A8_COOLDOWN_DAYS", "14")))

    # Claude
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))

    # Instagram (Instagram API with Instagram Login)
    ig_user_id: str = field(default_factory=lambda: _env("IG_USER_ID"))
    ig_access_token: str = field(default_factory=lambda: _env("IG_ACCESS_TOKEN"))
    ig_api_base: str = field(default_factory=lambda: _env("IG_API_BASE", "https://graph.instagram.com/v23.0"))
    ig_insight_metrics: str = field(
        default_factory=lambda: _env("IG_INSIGHT_METRICS", "reach,views,likes,comments,saved,shares")
    )

    # 公開URL (GitHub Pages など)。例: https://<user>.github.io/sns-affiliate
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL").rstrip("/"))
    site_title: str = field(default_factory=lambda: _env("SITE_TITLE", "おすすめアイテムまとめ"))
    account_name: str = field(default_factory=lambda: _env("IG_ACCOUNT_NAME", ""))

    # 商品画像をカードに載せるか。楽天の規約・画像の権利を確認したうえで有効化してください。
    use_product_image: bool = field(default_factory=lambda: _env_bool("USE_PRODUCT_IMAGE", False))


def load_genres() -> list[dict]:
    with GENRES_FILE.open(encoding="utf-8") as f:
        return json.load(f)["genres"]
