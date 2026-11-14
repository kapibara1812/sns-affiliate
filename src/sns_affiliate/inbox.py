"""自分で撮った写真の投稿ネタ（inbox/）を読み込む.

フォルダ構成:
    inbox/
      イヤホン/            ← フォルダ名は自由（日本語OK）
        memo.txt           ← 楽天の商品ページURL + 自分の感想（任意）
        1.jpg              ← 写真は任意。1枚目が表紙になる（ファイル名順）
        2.jpg
        ...

memo.txt の書き方で投稿の種類が決まる:
    - URL 1つ + 写真あり → 写真カルーセル投稿
    - URL 1つ + 写真なし → 文字カードの単品紹介
    - URL 2〜8つ         → まとめ・比較投稿（写真は使わない）
「ジャンル: gadget」「テーマ: 機内持ち込みできる旅行グッズ」の行で指定もできる。
使い終わったら memo.txt だけ inbox/_used/ に残し、元の写真は削除する
（投稿用の画像は docs/images/ に位置情報などのメタデータなしで書き出し済み）。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import config

INBOX_DIR = config.ROOT / "inbox"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTOS = 8  # 写真最大8枚 + ポイントカード1枚 = カルーセル上限10枚以内


MAX_ROUNDUP = 8


@dataclass
class InboxEntry:
    path: Path
    urls: list[str]
    comment: str
    genre_id: str | None
    theme: str = ""
    photos: list[Path] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def url(self) -> str:
        return self.urls[0]

    @property
    def is_roundup(self) -> bool:
        return len(self.urls) >= 2


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def parse_entry(folder: Path) -> InboxEntry | None:
    memo = folder / "memo.txt"
    if not memo.exists():
        return None
    urls: list[str] = []
    genre_id, theme, comment_lines = None, "", []
    for line in memo.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if url := re.search(r"https?://\S+", stripped):
            urls.append(url.group(0))
        elif m := re.match(r"^ジャンル\s*[:：]\s*(\S+)", stripped):
            genre_id = m.group(1)
        elif m := re.match(r"^テーマ\s*[:：]\s*(.+)$", stripped):
            theme = m.group(1).strip()
        else:
            comment_lines.append(line)
    if not urls:
        return None
    photos = sorted((p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXTS), key=_natural_key)
    return InboxEntry(path=folder, urls=list(dict.fromkeys(urls))[:MAX_ROUNDUP],
                      comment="\n".join(comment_lines).strip(), genre_id=genre_id, theme=theme,
                      photos=photos[:MAX_PHOTOS])


def list_entries(inbox_dir: Path | None = None) -> list[InboxEntry]:
    inbox_dir = inbox_dir or INBOX_DIR
    if not inbox_dir.exists():
        return []
    entries = []
    for folder in sorted(inbox_dir.iterdir(), key=_natural_key):
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            entry = parse_entry(folder)
            if entry:
                entries.append(entry)
    return entries


def mark_used(entry: InboxEntry, number: int) -> None:
    used = entry.path.parent / "_used"
    used.mkdir(exist_ok=True)
    shutil.copy2(entry.path / "memo.txt", used / f"{number:04d}_{entry.name}.txt")
    shutil.rmtree(entry.path)
