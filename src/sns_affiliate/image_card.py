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
    """できるだけ大きく、行数は少なく、最後の行が1〜2文字だけにならない組み方を探す."""
    one = _font(weight, sizes[0])
    if max_lines >= 2 and draw.textlength(text, font=one) > width:
        # 2行になるなら、読点などの言葉の切れ目で改行できる最大の大きさを優先する
        for size in sizes:
            if size < sizes[0] * 0.6:
                break
            f = _font(weight, size)
            if draw.textlength(text, font=f) <= width:
                break  # 1行に収まるなら通常の処理へ
            nb = _natural_break(draw, text, f, width)
            if nb:
                return f, nb
    for limit in range(1, max_lines + 1):
        for size in sizes:
            f = _font(weight, size)
            lines = _wrap(draw, text, f, width, limit + 1)
            if len(lines) > limit or "…" in lines[-1]:
                continue
            if len(lines) == 2:
                lines = _natural_break(draw, text, f, width) or _balance(draw, text, f, lines)
            else:
                lines = _even(draw, text, f, lines, width)
            if len(lines) > 1 and len(lines[-1]) <= 2:
                continue
            if size >= sizes[0] * 0.7 or limit == max_lines:
                return f, lines
    f = _font(weight, sizes[-1])
    return f, _wrap(draw, text, f, width, max_lines)


def _natural_break(draw, text: str, font, width: int) -> list[str] | None:
    """2行にするとき、読点や記号の直後など、言葉の切れ目で改行できるならそうする."""
    best = None
    for i in range(1, len(text)):
        if text[i - 1] not in "、。！？!?・ 　）」』":
            continue
        a, b = text[:i].rstrip(), text[i:].lstrip()
        wa, wb = draw.textlength(a, font=font), draw.textlength(b, font=font)
        if not a or not b or wa > width or wb > width or len(b) <= 2:
            continue
        score = abs(wa - wb)
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1] if best else None


def _even(draw, text: str, font, lines: list[str], width: int) -> list[str]:
    """3行以上のとき、各行の長さをそろえる（最後の行だけ極端に短くならないように）."""
    if len(lines) < 3:
        return lines
    n = len(lines)
    total = draw.textlength(text, font=font)
    target = total / n
    out, cur = [], ""
    for ch in text:
        if cur and (draw.textlength(cur + ch, font=font) > width or
                    (len(out) < n - 1 and draw.textlength(cur, font=font) >= target)):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out if len(out) == n and all(draw.textlength(l, font=font) <= width for l in out) else lines


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


# ---------------------------------------------------------------------------
# 図解カルーセル（写真がない A8 案件など）
# ---------------------------------------------------------------------------

WHITE = (255, 255, 255)
MUTED = (203, 213, 225)


def _canvas(genre: dict, number: int, right: str | None = None):
    bg = _hex(genre.get("color", "#0B1F3A"))
    accent = _hex(genre.get("accent", "#FF5A36"))
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    _header(draw, genre, number, bg, right=right)
    return img, draw, bg, accent


def _title(draw, text: str, y: int, accent, size_range=range(68, 47, -4)) -> int:
    font, lines = _fit_lines(draw, text, "bold", size_range, WIDTH - MARGIN * 2, 2)
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=WHITE)
        y += int(font.size * 1.3)
    draw.line((MARGIN, y + 14, MARGIN + 120, y + 14), fill=accent, width=6)
    return y + 60


def _footnote(draw, text: str) -> None:
    draw.text((MARGIN, HEIGHT - 36), text, font=_font("regular", 24), fill=MUTED, anchor="ls")


def _swipe(draw, accent) -> None:
    draw.text((WIDTH - MARGIN, HEIGHT - 36), "スワイプ →", font=_font("bold", 30), fill=accent, anchor="rs")


def render_explainer_cover(*, out_path: Path, number: int, genre: dict, hook: str, sub: str,
                           account_name: str = "") -> Path:
    img, draw, bg, accent = _canvas(genre, number)
    w = WIDTH - MARGIN * 2
    draw.rounded_rectangle((MARGIN, 220, MARGIN + 330, 292), radius=36, fill=accent)
    draw.text((MARGIN + 165, 256), "保存して見返してね", font=_font("bold", 32), fill=bg, anchor="mm")
    font, lines = _fit_lines(draw, hook, "bold", range(112, 67, -4), w, 3)
    lh = int(font.size * 1.28)
    block = lh * len(lines)
    y = max(360, (HEIGHT - block) // 2 - 60)
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=WHITE)
        y += lh
    sub_font, sub_lines = _fit_lines(draw, sub, "regular", range(46, 33, -2), w, 2)
    y += 30
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=sub_font, fill=MUTED)
        y += int(sub_font.size * 1.4)
    if account_name:
        draw.text((MARGIN, HEIGHT - 36), f"@{account_name}", font=_font("regular", 28), fill=MUTED, anchor="ls")
    _swipe(draw, accent)
    return _save(img, out_path)


def render_list_slide(*, out_path: Path, number: int, genre: dict, title: str, items: list[str],
                      mark: str = "check", footnote: str = "") -> Path:
    """箇条書きスライド。mark='pain' は困りごと（！）、'check' はポイント（✓）."""
    img, draw, bg, accent = _canvas(genre, number)
    y = _title(draw, title, 170, accent)
    avail = HEIGHT - 130 - y
    n = max(1, len(items))
    card_h = min(230, (avail - 34 * (n - 1)) // n)
    y += max(0, (avail - (card_h * n + 34 * (n - 1))) // 2)
    f = _font("bold", 52 if card_h >= 180 else 44)
    for item in items:
        panel = (MARGIN, y, WIDTH - MARGIN, y + card_h)
        draw.rounded_rectangle(panel, radius=28, fill=_mix(bg, WHITE, 0.08))
        cx, cy = MARGIN + 70, y + card_h // 2
        draw.ellipse((cx - 38, cy - 38, cx + 38, cy + 38), fill=accent)
        symbol = "!" if mark == "pain" else "✓"
        draw.text((cx, cy), symbol, font=_font("bold", 44), fill=bg, anchor="mm")
        lines = _wrap(draw, item, f, WIDTH - MARGIN * 2 - 170, 2)
        lh = int(f.size * 1.3)
        ty = cy - lh * len(lines) // 2
        for line in lines:
            draw.text((MARGIN + 140, ty), line, font=f, fill=WHITE)
            ty += lh
        y += card_h + 34
    if footnote:
        _footnote(draw, footnote)
    _swipe(draw, accent)
    return _save(img, out_path)


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render_compare_slide(*, out_path: Path, number: int, genre: dict, title: str, columns: list[str],
                         rows: list[tuple[str, list[str]]]) -> Path:
    """比較表。最後の列（紹介するサービスの種類）を強調する."""
    img, draw, bg, accent = _canvas(genre, number)
    y = _title(draw, title, 170, accent)
    label_w = 300
    col_w = (WIDTH - MARGIN * 2 - label_w) // len(columns)
    head_h, row_h = 120, min(190, (HEIGHT - 170 - y - 120) // max(1, len(rows)))
    y += max(0, (HEIGHT - 150 - y - head_h - row_h * len(rows)) // 2)
    hi = len(columns) - 1
    table_bottom = y + head_h + row_h * len(rows)
    hx = MARGIN + label_w + col_w * hi
    draw.rounded_rectangle((hx + 6, y - 10, hx + col_w - 6, table_bottom + 10), radius=24, fill=_mix(bg, accent, 0.22),
                           outline=accent, width=4)
    for i, col in enumerate(columns):
        cx = MARGIN + label_w + col_w * i + col_w // 2
        hf, lines = _fit_lines(draw, col, "bold", range(38, 25, -2), col_w - 24, 2)
        lh = int(hf.size * 1.25)
        ty = y + head_h // 2 - lh * len(lines) // 2
        for line in lines:
            draw.text((cx, ty), line, font=hf, fill=accent if i == hi else WHITE, anchor="mt")
            ty += lh
    y += head_h
    vf = _font("bold", 56)
    for label, values in rows:
        draw.line((MARGIN, y, WIDTH - MARGIN, y), fill=_mix(bg, WHITE, 0.25), width=2)
        cy = y + row_h // 2
        lf, llines = _fit_lines(draw, label, "bold", range(38, 25, -2), label_w - 24, 2)
        lh = int(lf.size * 1.25)
        ty = cy - lh * len(llines) // 2
        for line in llines:
            draw.text((MARGIN, ty), line, font=lf, fill=MUTED)
            ty += lh
        for i, v in enumerate(values):
            cx = MARGIN + label_w + col_w * i + col_w // 2
            font = vf if len(v) <= 1 else _font("bold", 34)
            t = _wrap(draw, v, font, col_w - 24, 1)
            draw.text((cx, cy), t[0] if t else "", font=font, fill=WHITE, anchor="mm")
        y += row_h
    _footnote(draw, "※一般的な傾向です。条件は各サービスでご確認ください")
    _swipe(draw, accent)
    return _save(img, out_path)


def render_steps_slide(*, out_path: Path, number: int, genre: dict, title: str,
                       steps: list[tuple[str, str]]) -> Path:
    img, draw, bg, accent = _canvas(genre, number)
    y = _title(draw, title, 170, accent)
    n = max(1, len(steps))
    avail = HEIGHT - 130 - y
    step_h = min(260, avail // n)
    y += max(0, (avail - step_h * n) // 2)
    tf, df = _font("bold", 58), _font("regular", 42)
    for i, (t, d) in enumerate(steps, start=1):
        cx, cy = MARGIN + 50, y + 50
        draw.ellipse((cx - 50, cy - 50, cx + 50, cy + 50), fill=accent)
        draw.text((cx, cy), str(i), font=_font("bold", 56), fill=bg, anchor="mm")
        if i < n:
            draw.line((cx, cy + 56, cx, y + step_h - 6), fill=_mix(bg, accent, 0.5), width=6)
        tl = _wrap(draw, t, tf, WIDTH - MARGIN * 2 - 140, 1)
        draw.text((MARGIN + 140, y + 12), tl[0] if tl else "", font=tf, fill=WHITE)
        dy = y + 96
        for line in _wrap(draw, d, df, WIDTH - MARGIN * 2 - 140, 2):
            draw.text((MARGIN + 140, dy), line, font=df, fill=MUTED)
            dy += 56
        y += step_h
    _footnote(draw, "※一般的な流れです。詳しい手順は公式サイトでご確認ください")
    _swipe(draw, accent)
    return _save(img, out_path)


def render_official_image_slide(*, out_path: Path, number: int, genre: dict, title: str,
                                image_url: str) -> Path | None:
    """広告主の公式画像素材を、切り抜き・加工せずに縮小だけして載せる。取得できなければ None."""
    official = _fetch_image(image_url)
    if official is None:
        return None
    img, draw, bg, accent = _canvas(genre, number)
    y = _title(draw, title, 170, accent)
    box_w, box_h = WIDTH - MARGIN * 2 - 80, HEIGHT - y - 220
    scale = min(box_w / official.width, box_h / official.height, 3.0)
    size = (max(1, int(official.width * scale)), max(1, int(official.height * scale)))
    official = official.resize(size, Image.LANCZOS)
    panel = (MARGIN, y, WIDTH - MARGIN, y + size[1] + 80)
    draw.rounded_rectangle(panel, radius=28, fill=WHITE)
    img.paste(official, ((WIDTH - size[0]) // 2, y + 40))
    draw.text((WIDTH // 2, panel[3] + 40), "画像：公式の広告素材", font=_font("regular", 28), fill=MUTED, anchor="mt")
    _swipe(draw, accent)
    return _save(img, out_path)


def render_cta_slide(*, out_path: Path, number: int, genre: dict, short_name: str, price_label: str,
                     account_name: str = "") -> Path:
    img, draw, bg, accent = _canvas(genre, number)
    w = WIDTH - MARGIN * 2
    draw.text((WIDTH // 2, 300), "気になったら", font=_font("bold", 56), fill=MUTED, anchor="mm")
    font, lines = _fit_lines(draw, short_name, "bold", range(96, 59, -4), w, 2)
    y = 400
    for line in lines:
        draw.text((WIDTH // 2, y), line, font=font, fill=WHITE, anchor="mt")
        y += int(font.size * 1.3)
    if price_label:
        pf, pl = _fit_lines(draw, price_label, "bold", range(72, 43, -4), w, 1)
        draw.text((WIDTH // 2, y + 50), pl[0], font=pf, fill=accent, anchor="mt")
        y += 50 + int(pf.size * 1.3)
    by = max(y + 90, 820)
    draw.rounded_rectangle((MARGIN, by, WIDTH - MARGIN, by + 110), radius=55, fill=accent)
    draw.text((WIDTH // 2, by + 55), f"プロフィールのリンク → #{number:03d}", font=_font("bold", 46), fill=bg, anchor="mm")
    draw.text((WIDTH // 2, by + 170), "保存しておくと、あとで見返せます", font=_font("regular", 36), fill=MUTED, anchor="mt")
    if account_name:
        draw.text((WIDTH - MARGIN, HEIGHT - 36), f"@{account_name}", font=_font("regular", 28), fill=MUTED, anchor="rs")
    _footnote(draw, "※条件・料金は公式サイトでご確認ください")
    return _save(img, out_path)
