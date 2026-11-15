"""Instagram 用の投稿画像（1080x1350, 4:5）を生成する."""

from __future__ import annotations

import io
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1350
MARGIN = 72

FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
    "regular": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[weight]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("日本語フォントが見つかりません。fonts-noto-cjk をインストールしてください")


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    """日本語は単語区切りがないので1文字ずつ詰めて折り返す."""
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n" or draw.textlength(current + ch, font=font) > max_width:
            lines.append(current)
            current = "" if ch == "\n" else ch
            if len(lines) == max_lines:
                break
        else:
            current += ch
    else:
        if current:
            lines.append(current)
    if len(lines) == max_lines and "".join(lines) != text.replace("\n", ""):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _balance(draw, text: str, font, lines: list[str]) -> list[str]:
    """2行に折り返す場合、1行目と2行目の長さが近くなる位置で改行する（1文字だけ残る等を防ぐ）."""
    if len(lines) != 2 or "\n" in text:
        return lines
    best = lines
    best_diff = abs(draw.textlength(lines[0], font=font) - draw.textlength(lines[1], font=font))
    for i in range(1, len(text)):
        a, b = text[:i], text[i:]
        if draw.textlength(a, font=font) > draw.textlength(lines[0], font=font):
            break
        diff = abs(draw.textlength(a, font=font) - draw.textlength(b, font=font))
        if diff < best_diff:
            best, best_diff = [a, b], diff
    return best


def _fetch_image(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def render_card(
    *,
    out_path: Path,
    number: int,
    genre: dict,
    headline: str,
    short_name: str,
    points: list[str],
    price: int,
    review_average: float,
    review_count: int,
    product_image_url: str | None = None,
    account_name: str = "",
    price_label: str | None = None,
    cta_verb: str = "購入は",
    footnote: str = "※価格は投稿時点のものです",
) -> Path:
    """price_label を渡すと価格欄をその文字列にする（A8.net のサービス案件など、価格が固定でない場合）."""
    bg = _hex(genre.get("color", "#1f2937"))
    accent = _hex(genre.get("accent", "#facc15"))
    white = (255, 255, 255)
    muted = (226, 232, 240)

    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)

    # 上部: PR表記 + ジャンル + 番号
    pr_font = _font("bold", 34)
    draw.rounded_rectangle((MARGIN, 60, MARGIN + 92, 112), radius=10, fill=white)
    draw.text((MARGIN + 46, 86), "PR", font=pr_font, fill=bg, anchor="mm")
    draw.text((MARGIN + 116, 86), genre["label"], font=pr_font, fill=accent, anchor="lm")
    draw.text((WIDTH - MARGIN, 86), f"#{number:03d}", font=_font("bold", 44), fill=white, anchor="rm")

    product = _fetch_image(product_image_url) if product_image_url else None
    content_w = WIDTH - MARGIN * 2
    footer_top = HEIGHT - 270

    # 見出し: 2行以内に収まる最大サイズを選び、行の長さを均等にする
    head_size, head_lines = 64, [headline]
    for size in range(100, 63, -4):
        f = _font("bold", size)
        lines = _wrap(draw, headline, f, content_w, 3)
        if len(lines) <= 2 and "…" not in lines[-1]:
            head_size, head_lines = size, _balance(draw, headline, f, lines)
            break
    else:
        head_lines = _wrap(draw, headline, _font("bold", head_size), content_w, 2)
    head_font = _font("bold", head_size)
    head_lh = int(head_size * 1.3)

    name_font = _font("regular", 40)
    point_font = _font("bold", 50 if product is None else 46)
    point_lh = 92 if product is None else 78
    pts = points[:3]

    if product is not None:
        y = 150
    else:  # 商品画像なし: 中央ブロックを上下中央に配置
        block_h = len(head_lines) * head_lh + 56 + 64 + 24 + len(pts) * point_lh
        y = 140 + max(0, (footer_top - 140 - block_h) // 2)

    for line in head_lines:
        draw.text((MARGIN, y), line, font=head_font, fill=white)
        y += head_lh
    y += 24

    if product is not None:
        box = 440
        product.thumbnail((box, box))
        panel = (MARGIN, y, WIDTH - MARGIN, y + box + 40)
        draw.rounded_rectangle(panel, radius=28, fill=white)
        img.paste(product, ((WIDTH - product.width) // 2, y + 20 + (box - product.height) // 2))
        y = panel[3] + 28
    else:
        draw.line((MARGIN, y, MARGIN + 120, y), fill=accent, width=6)
        y += 32

    for line in _wrap(draw, short_name, name_font, content_w, 1):
        draw.text((MARGIN, y), line, font=name_font, fill=muted)
        y += 64
    y += 24 if product is None else 0

    # 推しポイント
    for point in pts:
        r = point_font.size // 3
        cy = y + point_font.size // 2 + 6
        draw.ellipse((MARGIN, cy - r, MARGIN + 2 * r, cy + r), fill=accent)
        line = _wrap(draw, point, point_font, content_w - 56, 1)
        draw.text((MARGIN + 56, y), line[0] if line else "", font=point_font, fill=white)
        y += point_lh

    # 下部: 価格・レビュー・導線
    draw.line((MARGIN, footer_top, WIDTH - MARGIN, footer_top), fill=accent, width=3)
    label = price_label if price_label is not None else f"¥{price:,}"
    if label:
        lf = _font("bold", 64)
        lines = _wrap(draw, label, lf, (WIDTH - MARGIN * 2) * (0.55 if review_count else 1), 1)
        draw.text((MARGIN, footer_top + 30), lines[0] if lines else "", font=lf, fill=accent)
    if review_count:
        draw.text(
            (WIDTH - MARGIN, footer_top + 64),
            f"★{review_average:.1f}（{review_count:,}件）",
            font=_font("regular", 38),
            fill=white,
            anchor="rm",
        )
    cta = f"{cta_verb}プロフィールのリンク → #{number:03d}"
    draw.rounded_rectangle((MARGIN, footer_top + 120, WIDTH - MARGIN, footer_top + 190), radius=35, fill=accent)
    draw.text((WIDTH // 2, footer_top + 155), cta, font=_font("bold", 38), fill=bg, anchor="mm")
    if account_name:
        draw.text((WIDTH - MARGIN, HEIGHT - 36), f"@{account_name}", font=_font("regular", 28),
                  fill=muted, anchor="rs")
    draw.text((MARGIN, HEIGHT - 36), footnote, font=_font("regular", 24),
              fill=muted, anchor="ls")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)
    return out_path


# ---------------------------------------------------------------------------
# 自分で撮った写真を使うスライド
# ---------------------------------------------------------------------------

def _load_photo(path: Path) -> Image.Image:
    from PIL import ImageOps

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # スマホ写真の向きを補正
    return img.convert("RGB")


def _cover_crop(img: Image.Image) -> Image.Image:
    """4:5 に中央トリミングして 1080x1350 にする."""
    from PIL import ImageOps

    return ImageOps.fit(img, (WIDTH, HEIGHT), method=Image.LANCZOS, centering=(0.5, 0.45))


def _bottom_gradient(img: Image.Image, start: int, color=(0, 0, 0), max_alpha: int = 215) -> None:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(start, HEIGHT):
        a = int(max_alpha * min(1.0, (y - start) / max(1, (HEIGHT - start) * 0.6)))
        od.line((0, y, WIDTH, y), fill=(*color, a))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def render_photo_cover(*, photo: Path, out_path: Path, number: int, genre: dict, headline: str,
                       short_name: str) -> Path:
    """1枚目: 自分の写真に見出しを重ねた表紙."""
    img = _cover_crop(_load_photo(photo))
    accent = _hex(genre.get("accent", "#facc15"))
    white = (255, 255, 255)
    _bottom_gradient(img, HEIGHT - 620)
    draw = ImageDraw.Draw(img)

    # PR ラベルと番号（写真の上でも読めるよう半透明の座布団を敷く）
    draw.rounded_rectangle((MARGIN - 16, 44, MARGIN + 260, 128), radius=20, fill=(0, 0, 0))
    draw.rounded_rectangle((MARGIN, 60, MARGIN + 92, 112), radius=10, fill=white)
    draw.text((MARGIN + 46, 86), "PR", font=_font("bold", 34), fill=(0, 0, 0), anchor="mm")
    draw.text((MARGIN + 116, 86), f"#{number:03d}", font=_font("bold", 40), fill=white, anchor="lm")

    content_w = WIDTH - MARGIN * 2
    size, lines = 64, [headline]
    for s in range(96, 63, -4):
        f = _font("bold", s)
        ls = _wrap(draw, headline, f, content_w, 3)
        if len(ls) <= 2 and "…" not in ls[-1]:
            size, lines = s, _balance(draw, headline, f, ls)
            break
    else:
        lines = _wrap(draw, headline, _font("bold", size), content_w, 2)
    font = _font("bold", size)
    lh = int(size * 1.3)

    y = HEIGHT - 150 - lh * len(lines)
    draw.line((MARGIN, y - 36, MARGIN + 120, y - 36), fill=accent, width=6)
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=white)
        y += lh
    name = _wrap(draw, short_name, _font("regular", 38), content_w, 1)
    if name:
        draw.text((MARGIN, y + 16), name[0], font=_font("regular", 38), fill=(226, 232, 240))
    draw.text((WIDTH - MARGIN, HEIGHT - 40), "スワイプ →", font=_font("bold", 32), fill=accent, anchor="rs")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)
    return out_path


def render_photo_slide(*, photo: Path, out_path: Path, caption: str = "") -> Path:
    """2枚目以降: 写真をそのまま（任意で一言テロップ）."""
    img = _cover_crop(_load_photo(photo))
    if caption:
        _bottom_gradient(img, HEIGHT - 320, max_alpha=190)
        draw = ImageDraw.Draw(img)
        f = _font("bold", 52)
        for i, line in enumerate(_wrap(draw, caption, f, WIDTH - MARGIN * 2, 2)):
            draw.text((MARGIN, HEIGHT - 200 + i * 68), line, font=f, fill=(255, 255, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)
    return out_path


# ---------------------------------------------------------------------------
# まとめ・比較投稿のスライド
# ---------------------------------------------------------------------------

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _header(draw: ImageDraw.ImageDraw, genre: dict, number: int, bg, right: str | None = None) -> None:
    accent = _hex(genre.get("accent", "#facc15"))
    draw.rounded_rectangle((MARGIN, 60, MARGIN + 92, 112), radius=10, fill=(255, 255, 255))
    draw.text((MARGIN + 46, 86), "PR", font=_font("bold", 34), fill=bg, anchor="mm")
    draw.text((MARGIN + 116, 86), genre["label"], font=_font("bold", 34), fill=accent, anchor="lm")
    draw.text((WIDTH - MARGIN, 86), right or f"#{number:03d}", font=_font("bold", 44),
              fill=(255, 255, 255), anchor="rm")


def _fit_lines(draw, text: str, weight: str, sizes: range, width: int, max_lines: int):
    for size in sizes:
        f = _font(weight, size)
        lines = _wrap(draw, text, f, width, max_lines + 1)
        if len(lines) <= max_lines and "…" not in lines[-1]:
            return f, _balance(draw, text, f, lines)
    f = _font(weight, sizes[-1])
    return f, _wrap(draw, text, f, width, max_lines)


def _save(img: Image.Image, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)
    return out_path


def render_roundup_cover(*, out_path: Path, number: int, genre: dict, title: str, subtitle: str,
                         names: list[str], account_name: str = "") -> Path:
    bg = _hex(genre.get("color", "#1f2937"))
    accent = _hex(genre.get("accent", "#facc15"))
    white, muted = (255, 255, 255), (226, 232, 240)
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    _header(draw, genre, number, bg)
    w = WIDTH - MARGIN * 2

    # 「N選」バッジ
    draw.rounded_rectangle((MARGIN, 190, MARGIN + 250, 262), radius=36, fill=accent)
    draw.text((MARGIN + 125, 226), f"保存版 {len(names)}選", font=_font("bold", 38), fill=bg, anchor="mm")

    font, lines = _fit_lines(draw, title, "bold", range(104, 63, -4), w, 3)
    y = 300
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=white)
        y += int(font.size * 1.28)
    sub_font, sub = _fit_lines(draw, subtitle, "regular", range(44, 31, -2), w, 2)
    y += 16
    for line in sub:
        draw.text((MARGIN, y), line, font=sub_font, fill=muted)
        y += int(sub_font.size * 1.4)

    # 目次
    y = max(y + 40, 820)
    row_h = min(70, (HEIGHT - 150 - y) // max(1, len(names)))
    list_font = _font("regular", min(38, row_h - 22))
    draw.line((MARGIN, y - 20, WIDTH - MARGIN, y - 20), fill=accent, width=3)
    for i, name in enumerate(names):
        t = _wrap(draw, name, list_font, w - 70, 1)
        draw.text((MARGIN, y), CIRCLED[i], font=list_font, fill=accent)
        draw.text((MARGIN + 60, y), t[0] if t else "", font=list_font, fill=white)
        y += row_h
    draw.text((WIDTH - MARGIN, HEIGHT - 50), "スワイプ →", font=_font("bold", 36), fill=accent, anchor="rs")
    if account_name:
        draw.text((MARGIN, HEIGHT - 50), f"@{account_name}", font=_font("regular", 28), fill=muted, anchor="ls")
    return _save(img, out_path)


def render_roundup_item(*, out_path: Path, number: int, genre: dict, index: int, total: int,
                        short_name: str, best_for: str, points: list[str], price: int,
                        review_average: float, review_count: int,
                        product_image_url: str | None = None) -> Path:
    bg = _hex(genre.get("color", "#1f2937"))
    accent = _hex(genre.get("accent", "#facc15"))
    white, muted = (255, 255, 255), (226, 232, 240)
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    _header(draw, genre, number, bg, right=f"{index} / {total}")
    w = WIDTH - MARGIN * 2

    # 番号の丸 + 商品名
    draw.ellipse((MARGIN, 170, MARGIN + 120, 290), fill=accent)
    draw.text((MARGIN + 60, 230), str(index), font=_font("bold", 72), fill=bg, anchor="mm")
    name_font, name_lines = _fit_lines(draw, short_name, "bold", range(68, 43, -4), w - 150, 2)
    lh = int(name_font.size * 1.25)
    ny = 230 - lh * len(name_lines) // 2
    for line in name_lines:
        draw.text((MARGIN + 150, ny), line, font=name_font, fill=white)
        ny += lh
    y = 340

    product = _fetch_image(product_image_url) if product_image_url else None
    if product is not None:
        box = 400
        product.thumbnail((box, box))
        draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, y + box + 40), radius=28, fill=white)
        img.paste(product, ((WIDTH - product.width) // 2, y + 20 + (box - product.height) // 2))
        y += box + 80
    else:
        y += 60

    pf = _font("bold", 58 if product is None else 44)
    bf_font = _font("bold", 46 if product is None else 42)
    point_lines = [_wrap(draw, pt, pf, w - 56, 2) for pt in points[:2]]
    if product is None:  # 中央ブロック（向いている人 + 特徴）を上下中央に置く
        block_h = (150 if best_for else 0) + sum(len(ls) * int(pf.size * 1.35) + 36 for ls in point_lines)
        y = max(y, y + (HEIGHT - 300 - y - block_h) // 2)

    if best_for:
        t = _wrap(draw, best_for, bf_font, w - 60, 1)[0]
        tw = draw.textlength(t, font=bf_font)
        draw.rounded_rectangle((MARGIN, y, MARGIN + tw + 64, y + 84), radius=42, outline=accent, width=4)
        draw.text((MARGIN + 32, y + 42), t, font=bf_font, fill=accent, anchor="lm")
        y += 150

    for lines in point_lines:
        for j, line in enumerate(lines):
            if j == 0:
                r = pf.size // 3
                cy = y + pf.size // 2 + 6
                draw.ellipse((MARGIN, cy - r, MARGIN + 2 * r, cy + r), fill=accent)
            draw.text((MARGIN + 56, y), line, font=pf, fill=white)
            y += int(pf.size * 1.35)
        y += 36

    footer_top = HEIGHT - 270
    draw.line((MARGIN, footer_top, WIDTH - MARGIN, footer_top), fill=accent, width=3)
    draw.text((MARGIN, footer_top + 30), f"¥{price:,}", font=_font("bold", 64), fill=accent)
    if review_count:
        draw.text((WIDTH - MARGIN, footer_top + 64), f"★{review_average:.1f}（{review_count:,}件）",
                  font=_font("regular", 38), fill=white, anchor="rm")
    draw.rounded_rectangle((MARGIN, footer_top + 120, WIDTH - MARGIN, footer_top + 190), radius=35, fill=accent)
    draw.text((WIDTH // 2, footer_top + 155), f"購入はプロフィールのリンク → #{number:03d}-{index}",
              font=_font("bold", 36), fill=bg, anchor="mm")
    draw.text((MARGIN, HEIGHT - 36), "※価格は投稿時点のものです", font=_font("regular", 24), fill=muted, anchor="ls")
    return _save(img, out_path)


def render_roundup_summary(*, out_path: Path, number: int, genre: dict, rows: list[dict]) -> Path:
    """最後のスライド: 価格・評価の比較表（保存されやすい）."""
    bg = _hex(genre.get("color", "#1f2937"))
    accent = _hex(genre.get("accent", "#facc15"))
    white, muted = (255, 255, 255), (203, 213, 225)
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    _header(draw, genre, number, bg)
    draw.text((MARGIN, 170), "ひと目で比較", font=_font("bold", 76), fill=white)
    draw.text((MARGIN, 280), "保存しておくと買い物のときに便利です", font=_font("regular", 34), fill=muted)

    top, bottom = 370, HEIGHT - 250
    row_h = min(150, (bottom - top) // max(1, len(rows)))
    name_f, sub_f = _font("bold", min(42, row_h // 3)), _font("regular", min(32, row_h // 4))
    price_f = _font("bold", min(44, row_h // 3))
    y = top
    for i, r in enumerate(rows):
        draw.line((MARGIN, y, WIDTH - MARGIN, y), fill=(255, 255, 255, 60), width=1)
        cy = y + row_h // 2
        draw.text((MARGIN, cy), CIRCLED[i], font=price_f, fill=accent, anchor="lm")
        name = _wrap(draw, r["short_name"], name_f, 560, 1)
        draw.text((MARGIN + 70, cy - 4), name[0] if name else "", font=name_f, fill=white, anchor="ls")
        if r.get("best_for"):
            bf = _wrap(draw, r["best_for"], sub_f, 560, 1)
            draw.text((MARGIN + 70, cy + 10), bf[0] if bf else "", font=sub_f, fill=muted, anchor="lt")
        draw.text((WIDTH - MARGIN, cy - 4), f"¥{r['price']:,}", font=price_f, fill=accent, anchor="rs")
        if r.get("review_count"):
            draw.text((WIDTH - MARGIN, cy + 10), f"★{r['review_average']:.1f}（{r['review_count']:,}）",
                      font=sub_f, fill=muted, anchor="rt")
        y += row_h
    draw.line((MARGIN, y, WIDTH - MARGIN, y), fill=accent, width=3)

    footer_top = HEIGHT - 230
    draw.rounded_rectangle((MARGIN, footer_top + 50, WIDTH - MARGIN, footer_top + 130), radius=40, fill=accent)
    draw.text((WIDTH // 2, footer_top + 90), f"購入はプロフィールのリンク → #{number:03d}",
              font=_font("bold", 40), fill=bg, anchor="mm")
    draw.text((MARGIN, HEIGHT - 36), "※価格・評価は投稿時点のものです", font=_font("regular", 24),
              fill=muted, anchor="ls")
    return _save(img, out_path)
