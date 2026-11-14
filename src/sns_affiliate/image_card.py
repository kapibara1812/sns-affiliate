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
) -> Path:
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
    draw.text(
        (MARGIN, footer_top + 30),
        f"¥{price:,}",
        font=_font("bold", 64),
        fill=accent,
    )
    if review_count:
        draw.text(
            (WIDTH - MARGIN, footer_top + 64),
            f"★{review_average:.1f}（{review_count:,}件）",
            font=_font("regular", 38),
            fill=white,
            anchor="rm",
        )
    cta = f"購入はプロフィールのリンク → #{number:03d}"
    draw.rounded_rectangle((MARGIN, footer_top + 120, WIDTH - MARGIN, footer_top + 190), radius=35, fill=accent)
    draw.text((WIDTH // 2, footer_top + 155), cta, font=_font("bold", 38), fill=bg, anchor="mm")
    if account_name:
        draw.text((WIDTH - MARGIN, HEIGHT - 36), f"@{account_name}", font=_font("regular", 28),
                  fill=muted, anchor="rs")
    draw.text((MARGIN, HEIGHT - 36), "※価格は投稿時点のものです", font=_font("regular", 24),
              fill=muted, anchor="ls")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)
    return out_path
