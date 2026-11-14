"""プロフィールリンク用の一覧ページ（docs/index.html）を生成する.

Instagram のキャプション内リンクはクリックできないため、
「プロフィールのリンク → 投稿番号」で楽天の購入ページへ誘導する。
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from . import config

PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
:root {{ --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --accent:#bf0000; --line:#e2e8f0; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0b1120; --card:#111827; --text:#e5e7eb; --muted:#94a3b8; --accent:#f87171; --line:#1f2937; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; }}
main {{ max-width:560px; margin:0 auto; padding:20px 16px 48px; }}
h1 {{ font-size:20px; margin:8px 0 4px; }}
.note {{ font-size:12px; color:var(--muted); margin:0 0 16px; line-height:1.6; }}
input {{ width:100%; padding:12px 14px; font-size:16px; border:1px solid var(--line);
  border-radius:12px; background:var(--card); color:var(--text); margin-bottom:12px; }}
.chips {{ display:flex; gap:8px; overflow-x:auto; padding-bottom:12px; }}
.chip {{ flex:none; border:1px solid var(--line); background:var(--card); color:var(--text);
  border-radius:999px; padding:6px 12px; font-size:13px; cursor:pointer; }}
.chip[aria-pressed=true] {{ background:var(--text); color:var(--bg); }}
a.item {{ display:flex; gap:12px; align-items:center; text-decoration:none; color:inherit;
  background:var(--card); border:1px solid var(--line); border-radius:14px; padding:10px; margin-bottom:10px; }}
a.item img {{ width:84px; height:105px; object-fit:cover; border-radius:8px; flex:none; }}
.num {{ font-weight:700; color:var(--accent); font-size:13px; }}
.name {{ font-size:14px; line-height:1.4; margin:2px 0 4px; }}
.meta {{ font-size:12px; color:var(--muted); }}
.cta {{ margin-left:auto; flex:none; font-size:12px; font-weight:700; color:#fff; background:#bf0000;
  border-radius:999px; padding:8px 10px; }}
</style>
</head>
<body>
<main>
<h1>{title}</h1>
<p class="note">当ページは楽天アフィリエイトを利用した広告（PR）を含みます。価格・在庫は投稿時点の情報です。最新情報は販売ページでご確認ください。</p>
<input id="q" type="search" inputmode="numeric" placeholder="投稿の番号（例: 12）や商品名で検索">
<div class="chips" id="chips">{chips}</div>
<div id="list">
{items}
</div>
</main>
<script>
(function () {{
  var q = document.getElementById('q'), active = '';
  var items = Array.prototype.slice.call(document.querySelectorAll('a.item'));
  function apply() {{
    var v = q.value.trim().replace(/^#/, '').toLowerCase();
    items.forEach(function (el) {{
      var okText = !v || el.dataset.num === String(parseInt(v, 10)) || el.dataset.text.indexOf(v) >= 0;
      var okGenre = !active || el.dataset.genre === active;
      el.style.display = okText && okGenre ? '' : 'none';
    }});
  }}
  q.addEventListener('input', apply);
  document.getElementById('chips').addEventListener('click', function (e) {{
    var b = e.target.closest('.chip'); if (!b) return;
    active = active === b.dataset.genre ? '' : b.dataset.genre;
    document.querySelectorAll('.chip').forEach(function (c) {{
      c.setAttribute('aria-pressed', c.dataset.genre === active ? 'true' : 'false');
    }});
    apply();
  }});
  var m = location.hash.match(/^#(\\d+)$/); if (m) {{ q.value = m[1]; apply(); }}
}})();
</script>
</body>
</html>
"""

ITEM = """<a class="item" href="{url}" target="_blank" rel="sponsored noopener" data-num="{num}" data-genre="{genre}" data-text="{text}">
  <img src="{img}" alt="" loading="lazy">
  <div><div class="num">#{num3} ・ {genre_label}</div><div class="name">{name}</div><div class="meta">¥{price:,}</div></div>
  <span class="cta">楽天で見る</span>
</a>"""


def build_site(posts: list[dict], genres: list[dict], title: str, out_dir: Path = config.DOCS_DIR) -> Path:
    visible = [p for p in posts if p.get("status") in {"prepared", "published"}]
    visible.sort(key=lambda p: p["number"], reverse=True)
    labels = {g["id"]: g["label"] for g in genres}

    chips = "".join(
        f'<button class="chip" type="button" aria-pressed="false" data-genre="{escape(g["id"])}">{escape(g["label"])}</button>'
        for g in genres
    )
    items = "\n".join(
        ITEM.format(
            url=escape(p["affiliate_url"]),
            num=p["number"],
            num3=f"{p['number']:03d}",
            genre=escape(p["genre"]),
            genre_label=escape(labels.get(p["genre"], p["genre"])),
            text=escape(f"{p.get('short_name', '')} {p['item_name']}".lower()),
            img=escape(f"images/{Path(p['image_path']).name}"),
            name=escape(p.get("short_name") or p["item_name"]),
            price=p["price"],
        )
        for p in visible
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".nojekyll").touch()
    out = out_dir / "index.html"
    out.write_text(PAGE.format(title=escape(title), chips=chips, items=items), encoding="utf-8")
    return out
