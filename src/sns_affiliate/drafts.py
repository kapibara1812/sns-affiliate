"""③ 下書き: まとめて作り、人が直して承認したものだけを投稿する.

drafts/0001.md のような文章ファイルを1本1ファイルで置く。
- 「承認: はい」にしたものだけが、毎日1本ずつ投稿される（予定日があればその日以降）
- 文章を直すと、確認ページの画像も作り直される（投稿時にも直した文章から画像を作る）
- 投稿したファイルは drafts/_done/ に移る
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path

from . import config, storage
from .caption import ExplainerCopy
from .image_card import (render_compare_slide, render_cta_slide, render_explainer_cover, render_list_slide,
                         render_official_image_slide, render_steps_slide)

KIND_LABELS = {"a8": "A8.net 案件", "rakuten": "楽天の商品", "tips": "お役立ち（広告なし）"}
_KIND_FROM_LABEL = {v: k for k, v in KIND_LABELS.items()}


@dataclass
class Draft:
    id: int
    kind: str                      # a8 / rakuten / tips
    genre_id: str
    subject: str                   # 案件ID（a8）、商品コード（rakuten）、テーマ（tips）
    name: str                      # 表示名
    copy: ExplainerCopy
    approved: bool = False
    scheduled: str = ""            # 予定日（YYYY-MM-DD、空なら承認された順）
    link: str = ""                 # アフィリエイトリンク
    impression: str = ""
    price_label: str = ""
    images: list[str] = field(default_factory=list)
    pattern: str = ""
    audience: str = ""
    remix_of: int | None = None    # 作り直しの元になった投稿番号
    state: str = "下書き"          # 下書き / 投稿済み #NNN
    path: Path | None = None

    @property
    def filename(self) -> str:
        return f"{self.id:04d}.md"


# ---------------------------------------------------------------------------
# 文章ファイル ⇔ Draft
# ---------------------------------------------------------------------------

def to_markdown(d: Draft) -> str:
    c = d.copy
    meta = [
        f"承認: {'はい' if d.approved else 'いいえ'}",
        f"予定日: {d.scheduled}",
        f"状態: {d.state}",
        f"種類: {KIND_LABELS.get(d.kind, d.kind)}",
        f"ジャンル: {d.genre_id}",
        f"対象: {d.subject}",
        f"名前: {d.name}",
        f"価格表示: {d.price_label}",
        f"リンク: {d.link}",
        f"計測: {d.impression}",
        f"公式画像: {' '.join(d.images)}",
        f"型: {d.pattern}",
        f"相手: {d.audience}",
        f"作り直し元: {d.remix_of if d.remix_of else ''}",
    ]
    lines = [
        "<!-- この下書きを直すときは、各行の「:」より右側や、「- 」で始まる行を書き換えてください。",
        "     投稿してよければ、1行目を「承認: はい」にして保存（Commit changes）してください。 -->",
        *meta,
        "",
        "## 表紙",
        f"大見出し: {c.cover_hook}",
        f"小見出し: {c.cover_sub}",
        "",
        "## 悩み",
        f"見出し: {c.pains_title}",
        *[f"- {x}" for x in c.pains],
        "",
        "## 比較表",
        f"見出し: {c.compare_title}",
        f"列: {' | '.join(c.compare_columns)}",
        *[f"- {label} | {' | '.join(values)}" for label, values in c.compare_rows],
        "",
        "## 手順",
        f"見出し: {c.steps_title}",
        *[f"- {t} / {desc}" for t, desc in c.steps],
        "",
        "## ポイント",
        f"呼び名: {c.short_name}",
        *[f"- {x}" for x in c.features],
        "",
        "## こんな人に",
        *[f"- {x}" for x in c.fit],
        "",
        "## キャプション",
        c.caption,
        "",
        "## ハッシュタグ",
        " ".join(c.hashtags),
        "",
    ]
    return "\n".join(lines)


def _kv(line: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*([^:：]+?)\s*[:：]\s?(.*)$", line)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def from_markdown(text: str, path: Path | None = None) -> Draft:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    meta: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is None:
            kv = _kv(line)
            if kv:
                meta[kv[0]] = kv[1]
        else:
            sections[current].append(line)

    def kvs(name: str) -> dict[str, str]:
        out = {}
        for line in sections.get(name, []):
            if not line.lstrip().startswith("- "):
                kv = _kv(line)
                if kv:
                    out[kv[0]] = kv[1]
        return out

    def items(name: str) -> list[str]:
        return [l.strip()[2:].strip() for l in sections.get(name, []) if l.strip().startswith("- ") and l.strip()[2:].strip()]

    cmp = kvs("比較表")
    columns = [c.strip() for c in cmp.get("列", "").split("|") if c.strip()]
    rows = []
    for it in items("比較表"):
        parts = [x.strip() for x in it.split("|")]
        if columns and len(parts) >= len(columns) + 1:
            rows.append((parts[0], parts[1 : len(columns) + 1]))
    if len(columns) < 2 or not rows:
        columns, rows = [], []
    steps = []
    for it in items("手順"):
        t, _, desc = it.partition("/")
        steps.append((t.strip(), desc.strip()))
    caption = "\n".join(sections.get("キャプション", [])).strip()
    tags = " ".join(sections.get("ハッシュタグ", [])).split()
    copy = ExplainerCopy(
        cover_hook=kvs("表紙").get("大見出し", ""),
        cover_sub=kvs("表紙").get("小見出し", ""),
        short_name=kvs("ポイント").get("呼び名", "") or meta.get("名前", ""),
        pains_title=kvs("悩み").get("見出し", "") or "こんな悩み、ありませんか？",
        pains=items("悩み")[:3],
        compare_title=cmp.get("見出し", "") if rows else "",
        compare_columns=columns[:3],
        compare_rows=[(l, v[:3]) for l, v in rows][:4],
        steps_title=kvs("手順").get("見出し", "") if steps else "",
        steps=steps[:4],
        features=items("ポイント")[:4],
        fit=items("こんな人に")[:3],
        caption=caption,
        hashtags=[t if t.startswith("#") else f"#{t}" for t in tags],
    )
    stem = path.stem if path else ""
    draft_id = int(stem) if stem.isdigit() else 0
    remix = meta.get("作り直し元", "").strip().lstrip("#")
    return Draft(
        id=draft_id,
        kind=_KIND_FROM_LABEL.get(meta.get("種類", ""), meta.get("種類", "tips") or "tips"),
        genre_id=meta.get("ジャンル", ""),
        subject=meta.get("対象", ""),
        name=meta.get("名前", ""),
        copy=copy,
        approved=meta.get("承認", "").strip() in {"はい", "yes", "YES", "OK", "ok", "済", "承認"},
        scheduled=meta.get("予定日", "").strip(),
        link=meta.get("リンク", ""),
        impression=meta.get("計測", ""),
        price_label=meta.get("価格表示", ""),
        images=meta.get("公式画像", "").split(),
        pattern=meta.get("型", ""),
        audience=meta.get("相手", ""),
        remix_of=int(remix) if remix.isdigit() else None,
        state=meta.get("状態", "下書き") or "下書き",
        path=path,
    )


# ---------------------------------------------------------------------------
# 保存・読み込み
# ---------------------------------------------------------------------------

def list_drafts(include_done: bool = False) -> list[Draft]:
    base = config.DRAFTS_DIR
    paths = sorted(base.glob("[0-9]*.md")) if base.exists() else []
    if include_done and (base / "_done").exists():
        paths += sorted((base / "_done").glob("[0-9]*.md"))
    return [from_markdown(p.read_text(encoding="utf-8"), p) for p in paths]


def next_id() -> int:
    ids = [d.id for d in list_drafts(include_done=True)]
    return max(ids, default=0) + 1


def save(d: Draft) -> Path:
    path = d.path or (config.DRAFTS_DIR / d.filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(d), encoding="utf-8")
    d.path = path
    return path


def mark_published(d: Draft, number: int) -> None:
    d.state = f"投稿済み #{number:03d}"
    done = config.DRAFTS_DIR / "_done"
    done.mkdir(parents=True, exist_ok=True)
    old = d.path
    d.path = done / d.filename
    save(d)
    if old and old.exists() and old != d.path:
        old.unlink()


def next_approved(today: date | None = None) -> Draft | None:
    today = today or storage.datetime.now(storage.JST).date()
    ready = []
    for d in list_drafts():
        if not d.approved or not d.state.startswith("下書き"):
            continue
        if d.scheduled:
            try:
                if date.fromisoformat(d.scheduled) > today:
                    continue
            except ValueError:
                pass
        ready.append(d)
    ready.sort(key=lambda d: (d.scheduled or "9999", d.id))
    return ready[0] if ready else None


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------

def genre_for(d: Draft) -> dict:
    genres = {g["id"]: g for g in config.load_genres(include_disabled=True)}
    g = dict(genres.get(d.genre_id) or config.load_genres()[0])
    if d.kind == "tips":
        g["pr"] = False
    return g


def render(d: Draft, out_dir: Path, number: int, account_name: str = "", prefix: str = "") -> list[Path]:
    """表紙 → 悩み → 比較 → 手順 → 公式画像 → ポイント → こんな人に → まとめ（最大8枚）."""
    ex, genre = d.copy, genre_for(d)
    n = [0]

    def out() -> Path:
        n[0] += 1
        return out_dir / f"{prefix}{n[0]:02d}.jpg"

    slides = [render_explainer_cover(out_path=out(), number=number, genre=genre, hook=ex.cover_hook,
                                     sub=ex.cover_sub, account_name=account_name)]
    if ex.pains:
        slides.append(render_list_slide(out_path=out(), number=number, genre=genre, title=ex.pains_title,
                                        items=ex.pains, mark="pain"))
    if ex.compare_rows:
        slides.append(render_compare_slide(out_path=out(), number=number, genre=genre, title=ex.compare_title,
                                           columns=ex.compare_columns, rows=ex.compare_rows))
    if ex.steps:
        slides.append(render_steps_slide(out_path=out(), number=number, genre=genre, title=ex.steps_title,
                                         steps=ex.steps))
    for url in d.images[:1]:
        path = render_official_image_slide(out_path=out_dir / f"{prefix}{n[0] + 1:02d}.jpg", number=number,
                                           genre=genre, title=ex.short_name, image_url=url)
        if path is not None:
            slides.append(path)
            n[0] += 1
    title = f"{ex.short_name}のポイント" if d.kind != "tips" else "ポイントまとめ"
    note = "※公式サイト・広告主の情報をもとにしています" if d.kind != "tips" else ""
    if ex.features:
        slides.append(render_list_slide(out_path=out(), number=number, genre=genre, title=title, items=ex.features,
                                        mark="check", footnote=note))
    if ex.fit:
        slides.append(render_list_slide(out_path=out(), number=number, genre=genre, title="こんな人におすすめ",
                                        items=ex.fit, mark="check"))
    if d.kind != "tips":
        slides.append(render_cta_slide(out_path=out(), number=number, genre=genre, short_name=ex.short_name,
                                       price_label=d.price_label, account_name=account_name))
    return slides


# ---------------------------------------------------------------------------
# スマホ用の確認ページ（docs/drafts/index.html）
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>下書きの確認</title>
<style>
:root {{ --bg:#f6f7fb; --card:#fff; --text:#1e2a4a; --muted:#64748b; --ok:#16a34a; --wait:#d97706; --line:#e2e8f0; --accent:#d7262b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0b1120; --card:#111827; --text:#e5e7eb; --muted:#94a3b8; --line:#1f2937; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif; }}
main {{ max-width:620px; margin:0 auto; padding:16px 16px 60px; }}
h1 {{ font-size:20px; margin:6px 0 2px; }}
.lead {{ color:var(--muted); font-size:13px; line-height:1.6; margin:0 0 14px; }}
.tabs {{ display:flex; gap:8px; margin-bottom:14px; }}
.tab {{ flex:1; border:1px solid var(--line); background:var(--card); color:var(--text); border-radius:999px; padding:8px; font-size:14px; }}
.tab[aria-pressed=true] {{ background:var(--text); color:var(--bg); }}
article {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px; margin-bottom:16px; }}
.head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:13px; }}
.badge {{ font-weight:700; border-radius:999px; padding:3px 10px; color:#fff; }}
.ok {{ background:var(--ok); }} .wait {{ background:var(--wait); }} .done {{ background:var(--muted); }}
.meta {{ color:var(--muted); }}
h2 {{ font-size:17px; margin:10px 0 8px; line-height:1.4; }}
.slides {{ display:flex; gap:8px; overflow-x:auto; scroll-snap-type:x mandatory; padding-bottom:6px; }}
.slides img {{ width:78%; max-width:360px; flex:none; border-radius:10px; scroll-snap-align:start; border:1px solid var(--line); }}
details {{ margin-top:10px; }}
summary {{ cursor:pointer; font-size:14px; }}
pre {{ white-space:pre-wrap; font-family:inherit; font-size:14px; line-height:1.7; background:var(--bg); padding:10px; border-radius:10px; }}
.actions {{ display:flex; gap:8px; margin-top:12px; }}
.btn {{ flex:1; text-align:center; text-decoration:none; font-weight:700; border-radius:12px; padding:12px; font-size:15px; }}
.edit {{ background:var(--accent); color:#fff; }}
.raw {{ border:1px solid var(--line); color:var(--text); }}
</style></head><body><main>
<h1>下書きの確認</h1>
<p class="lead">画像を横にスクロールして確認できます。直したいところがあれば「直す・承認する」から編集し、
問題なければ1行目を「承認: はい」にして保存してください。承認した下書きだけが、毎日1本ずつ投稿されます。<br>
保存してから数分で、このページの画像も新しい文章で作り直されます。</p>
<div class="tabs"><button class="tab" data-f="wait" aria-pressed="true">承認待ち {n_wait}</button>
<button class="tab" data-f="ok" aria-pressed="false">承認済み {n_ok}</button>
<button class="tab" data-f="done" aria-pressed="false">投稿済み {n_done}</button></div>
{cards}
</main>
<script>
(function () {{
  var tabs = document.querySelectorAll('.tab'), cards = document.querySelectorAll('article');
  function show(f) {{
    tabs.forEach(function (t) {{ t.setAttribute('aria-pressed', t.dataset.f === f ? 'true' : 'false'); }});
    cards.forEach(function (c) {{ c.style.display = c.dataset.s === f ? '' : 'none'; }});
  }}
  tabs.forEach(function (t) {{ t.addEventListener('click', function () {{ show(t.dataset.f); }}); }});
  show('wait');
}})();
</script></body></html>
"""

CARD = """<article data-s="{status}">
<div class="head"><span class="badge {status}">{badge}</span><span class="meta">下書き {id:04d}・{kind}・{genre}{sched}</span></div>
<h2>{hook}</h2>
<div class="slides">{imgs}</div>
<details><summary>キャプションを見る</summary><pre>{caption}</pre></details>
<div class="meta" style="font-size:12px;margin-top:6px">型：{pattern}／相手：{audience}{remix}</div>
<div class="actions">{edit}<a class="btn raw" href="{raw}">文章ファイル</a></div>
</article>"""


def preview_dir(d: Draft) -> Path:
    return config.DRAFT_PREVIEW_DIR / f"{d.id:04d}"


def render_preview(d: Draft, account_name: str = "") -> list[Path]:
    out = preview_dir(d)
    if out.exists():
        for f in out.glob("*.jpg"):
            f.unlink()
    return render(d, out, number=0, account_name=account_name)


def build_page(repo_slug: str = "", branch: str = "main") -> Path:
    drafts = list_drafts(include_done=True)
    genres = {g["id"]: g["label"] for g in config.load_genres(include_disabled=True)}
    cards, counts = [], {"wait": 0, "ok": 0, "done": 0}
    for d in sorted(drafts, key=lambda d: d.id, reverse=True):
        status = "done" if not d.state.startswith("下書き") else ("ok" if d.approved else "wait")
        counts[status] += 1
        imgs = "".join(f'<img src="{d.id:04d}/{p.name}" alt="" loading="lazy">'
                       for p in sorted(preview_dir(d).glob("*.jpg")))
        rel = d.path.relative_to(config.ROOT).as_posix() if d.path else f"drafts/{d.filename}"
        edit = (f'<a class="btn edit" href="https://github.com/{repo_slug}/edit/{branch}/{rel}">直す・承認する</a>'
                if repo_slug and status != "done" else "")
        raw = f"https://github.com/{repo_slug}/blob/{branch}/{rel}" if repo_slug else rel
        cards.append(CARD.format(
            status=status, badge={"wait": "承認待ち", "ok": "承認済み", "done": d.state}[status], id=d.id,
            kind=escape(KIND_LABELS.get(d.kind, d.kind)), genre=escape(genres.get(d.genre_id, d.genre_id)),
            sched=escape(f"・予定日 {d.scheduled}") if d.scheduled else "", hook=escape(d.copy.cover_hook),
            imgs=imgs, caption=escape(d.copy.caption), pattern=escape(d.pattern), audience=escape(d.audience),
            remix=escape(f"／#{d.remix_of:03d} の作り直し") if d.remix_of else "", edit=edit, raw=escape(raw)))
    out = config.DRAFT_PREVIEW_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.format(cards="\n".join(cards), n_wait=counts["wait"], n_ok=counts["ok"],
                               n_done=counts["done"]), encoding="utf-8")
    return out
