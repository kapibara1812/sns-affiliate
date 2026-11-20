"""A8.net の案件（プログラム）を扱う.

A8.net には商品を自動で検索する公開APIがないため、提携した案件を
config/a8_programs.json に手で登録して使う（書き方は config/a8_programs.example.json）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import config, storage

PROGRAMS_FILE = config.ROOT / "config" / "a8_programs.json"


@dataclass
class A8Program:
    id: str
    name: str
    genre: str
    link: str
    facts: list[str]
    sns_allowed: bool = False
    impression: str = ""
    price_label: str = ""
    ng_words: list[str] = field(default_factory=list)
    notes: str = ""
    enabled: bool = True
    images: list[str] = field(default_factory=list)  # 広告主の公式画像素材（バナー等）の URL

    @property
    def item_code(self) -> str:
        return f"a8:{self.id}"

    @classmethod
    def from_dict(cls, d: dict) -> "A8Program":
        missing = [k for k in ("id", "name", "genre", "link", "facts") if not d.get(k)]
        if missing:
            raise ValueError(f"A8案件 {d.get('id', '?')} に必須項目がありません: {', '.join(missing)}")
        if "a8.net" not in d["link"]:
            raise ValueError(f"A8案件 {d['id']}: link が A8.net のアフィリエイトリンクではありません")
        return cls(
            id=d["id"], name=d["name"], genre=d["genre"], link=d["link"], facts=list(d["facts"]),
            sns_allowed=bool(d.get("sns_allowed", False)), impression=d.get("impression", ""),
            price_label=d.get("price_label", ""), ng_words=list(d.get("ng_words", [])),
            notes=d.get("notes", ""), enabled=bool(d.get("enabled", True)),
            images=[u for u in d.get("images", []) if u],
        )


def load_programs(path: Path | None = None) -> list[A8Program]:
    path = path or PROGRAMS_FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return [A8Program.from_dict(d) for d in data.get("programs", [])]


def usable(programs: list[A8Program]) -> list[A8Program]:
    """有効かつ SNS での紹介が認められている案件だけ."""
    return [p for p in programs if p.enabled and p.sns_allowed]


def get_program(programs: list[A8Program], program_id: str) -> A8Program:
    for p in programs:
        if p.id == program_id:
            if not p.sns_allowed:
                raise RuntimeError(
                    f"A8案件 {program_id} は sns_allowed が true になっていません。"
                    "A8.net の提携条件で Instagram での紹介が認められているか確認してから true にしてください")
            return p
    raise RuntimeError(f"A8案件 {program_id} が config/a8_programs.json にありません")


def pick_program(programs: list[A8Program], posts: list[dict], cooldown_days: int,
                 genre_id: str | None = None) -> A8Program | None:
    """最後に投稿してから一番時間が経っている案件を選ぶ（cooldown 内のものは除く）."""
    last: dict[str, datetime] = {}
    for p in posts:
        if p.get("network") == "a8" and p.get("status") != "failed":
            last[p["item_code"]] = max(last.get(p["item_code"], datetime.min.replace(tzinfo=storage.JST)),
                                       datetime.fromisoformat(p["created_at"]))
    cutoff = datetime.now(storage.JST) - timedelta(days=cooldown_days)
    candidates = [p for p in usable(programs)
                  if (genre_id is None or p.genre == genre_id) and last.get(p.item_code, cutoff) <= cutoff]
    if not candidates:
        return None
    floor = datetime.min.replace(tzinfo=storage.JST)
    return min(candidates, key=lambda p: last.get(p.item_code, floor))


def find_ng_words(program: A8Program, *texts: str) -> list[str]:
    joined = "\n".join(texts)
    return [w for w in program.ng_words if w and w in joined]
