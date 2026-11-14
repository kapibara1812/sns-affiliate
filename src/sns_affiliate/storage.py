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
    return {
        p["item_code"]
        for p in posts
        if p.get("status") != "failed" and datetime.fromisoformat(p["created_at"]) >= cutoff
    }
