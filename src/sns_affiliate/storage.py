"""投稿履歴 (data/posts.json) の読み書き."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

JST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def load_posts(path: Path = config.POSTS_FILE) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_posts(posts: list[dict], path: Path = config.POSTS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def next_number(posts: list[dict]) -> int:
    return max((p["number"] for p in posts), default=0) + 1


def recently_posted_codes(posts: list[dict], cooldown_days: int) -> set[str]:
    cutoff = datetime.now(JST) - timedelta(days=cooldown_days)
    codes: set[str] = set()
    for p in posts:
        if p.get("status") == "failed" or datetime.fromisoformat(p["created_at"]) < cutoff:
            continue
        codes.update(it["item_code"] for it in post_items(p))
    return codes


def post_items(post: dict) -> list[dict]:
    """単品投稿もまとめ投稿も、商品のリストとして扱う."""
    if post.get("kind") == "roundup":
        return post["items"]
    return [{k: post.get(k) for k in ("item_code", "item_name", "short_name", "price", "review_average",
                                      "review_count", "item_url", "affiliate_url", "network", "impression",
                                      "price_label")}
            | {"image": post["image_path"]}]
