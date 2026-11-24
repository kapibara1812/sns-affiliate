"""① 調査: 伸びている投稿を集めた CSV から、刺さる1行目の型をまとめる.

research/posts.csv（Claude in Chrome などで集めたもの）を読み、AI に共通点を5つの型にまとめさせて
research/patterns.json（プログラム用）と research/patterns.md（読む用）に保存する。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from . import config

RESEARCH_DIR = config.ROOT / "research"
POSTS_CSV = RESEARCH_DIR / "posts.csv"
PATTERNS_JSON = RESEARCH_DIR / "patterns.json"
PATTERNS_MD = RESEARCH_DIR / "patterns.md"

# 何も調査していないときに使う、一般的な型（調査結果があればそちらを優先）
DEFAULT_PATTERNS = [
    {"name": "損を突く", "template": "{ジャンル}選びで損してない？知らないと後悔する{数字}つのこと",
     "why": "損失回避。知らないと損だと感じると、続きを見て保存したくなる", "examples": []},
    {"name": "数字で比べる", "template": "{商品A}と{商品B}、{観点}で比べたら差がついた", "why": "比較は保存されやすい", "examples": []},
    {"name": "初心者に寄り添う", "template": "{ジャンル}初心者が最初にそろえるべき{数字}つ", "why": "対象がはっきりして自分ごとになる",
     "examples": []},
    {"name": "あるあるの悩み", "template": "{悩み}…それ、{原因}かも", "why": "共感から入ると最後まで読まれやすい", "examples": []},
    {"name": "結論から言う", "template": "結論：{ジャンル}は{条件}で選べばOK", "why": "答えが先にあると安心して読める", "examples": []},
]


def _pick(row: dict, *keys: str) -> str:
    for k in row:
        if any(key in k for key in keys):
            return (row[k] or "").strip()
    return ""


def _num(text: str) -> float:
    t = text.replace(",", "").replace("件", "").strip()
    mult = 1
    if t.endswith("万"):
        mult, t = 10000, t[:-1]
    elif t.lower().endswith("k"):
        mult, t = 1000, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return 0.0


def load_posts_csv(path: Path | None = None) -> list[dict]:
    """列名は多少ゆれていても読めるようにする（見出し・1行目・いいね・保存・再生・構成など）."""
    path = path or POSTS_CSV
    text = path.read_text(encoding="utf-8-sig")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append({
            "account": _pick(row, "アカウント"),
            "type": _pick(row, "種類"),
            "headline": _pick(row, "見出し", "表紙"),
            "first_line": _pick(row, "1行目", "一行目"),
            "likes": _num(_pick(row, "いいね")),
            "saves": _num(_pick(row, "保存")),
            "views": _num(_pick(row, "再生")),
            "structure": _pick(row, "構成"),
            "design": _pick(row, "デザイン"),
        })
    return [r for r in rows if r["headline"] or r["first_line"]]


def rows_to_text(rows: list[dict]) -> str:
    rows = sorted(rows, key=lambda r: (r["saves"], r["views"], r["likes"]), reverse=True)
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. [{r['type'] or '?'}] いいね{int(r['likes'])} 保存{int(r['saves'])} 再生{int(r['views'])} / "
            f"表紙: {r['headline']} / 1行目: {r['first_line']} / 構成: {r['structure']} / デザイン: {r['design']}")
    return "\n".join(lines)


def analyze(writer, genre_label: str, path: Path | None = None) -> dict:
    rows = load_posts_csv(path)
    if not rows:
        raise RuntimeError("research/posts.csv に投稿が見つかりません（見出しか1行目の列が必要です）")
    result = writer.analyze_patterns(rows_to_text(rows), genre_label)
    result["source_count"] = len(rows)
    save_patterns(result)
    return result


def save_patterns(result: dict) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    PATTERNS_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [f"# 刺さる1行目の型（{result.get('source_count', '?')}本の投稿から）", "", result.get("summary", ""), ""]
    for i, p in enumerate(result["patterns"], 1):
        md += [f"## {i}. {p['name']}", f"- テンプレート：{p['template']}", f"- 理由：{p['why']}"]
        md += [f"- 例：{e}" for e in p.get("examples", [])] + [""]
    if result.get("structures"):
        md += ["## よくある構成"] + [f"- {x}" for x in result["structures"]] + [""]
    if result.get("design_notes"):
        md += ["## デザインの共通点"] + [f"- {x}" for x in result["design_notes"]] + [""]
    PATTERNS_MD.write_text("\n".join(md), encoding="utf-8")


def load_patterns() -> list[dict]:
    if PATTERNS_JSON.exists():
        data = json.loads(PATTERNS_JSON.read_text(encoding="utf-8"))
        if data.get("patterns"):
            return data["patterns"]
    return DEFAULT_PATTERNS
