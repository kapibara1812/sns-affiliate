"""自分で撮った写真の投稿ネタ（inbox/）を読み込む.

フォルダ構成:
    inbox/
      イヤホン/            ← フォルダ名は自由（日本語OK）
        memo.txt           ← 1行目: 楽天の商品ページURL / 2行目以降: 自分の感想（任意）
        1.jpg              ← 1枚目が表紙になる（ファイル名順）
        2.jpg
        ...

memo.txt には「ジャンル: gadget」の行を入れるとジャンルを指定できる（省略時はローテーション順）。
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


@dataclass
class InboxEntry:
    path: Path
    url: str
    comment: str
    genre_id: str | None
    photos: list[Path] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def parse_entry(folder: Path) -> InboxEntry | None:
    memo = folder / "memo.txt"
    if not memo.exists():
        return None
    url, genre_id, comment_lines = "", None, []
    for line in memo.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not url and stripped.startswith("http"):
            url = stripped
        elif m := re.match(r"^ジャンル\s*[:：]\s*(\S+)", stripped):
            genre_id = m.group(1)
        else:
            comment_lines.append(line)
    if not url:
        return None
    photos = sorted((p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXTS), key=_natural_key)
    return InboxEntry(path=folder, url=url, comment="\n".join(comment_lines).strip(),
                      genre_id=genre_id, photos=photos[:MAX_PHOTOS])


def list_entries(inbox_dir: Path | None = None) -> list[InboxEntry]:
    inbox_dir = inbox_dir or INBOX_DIR
    if not inbox_dir.exists():
        return []
    entries = []
    for folder in sorted(inbox_dir.iterdir(), key=_natural_key):
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            entry = parse_entry(folder)
            if entry and entry.photos:
                entries.append(entry)
    return entries


def mark_used(entry: InboxEntry, number: int) -> None:
    used = entry.path.parent / "_used"
    used.mkdir(exist_ok=True)
    shutil.copy2(entry.path / "memo.txt", used / f"{number:04d}_{entry.name}.txt")
    shutil.rmtree(entry.path)
