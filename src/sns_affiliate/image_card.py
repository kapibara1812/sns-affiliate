"""Instagram 用の投稿画像（1080x1350, 4:5）を生成する.

デザインの方針（「手作り感のある、やさしい」見た目）:
- 背景はクリーム色の紙（うっすら方眼の点と紙の粒子）
- 見出しは手書き風フォント（Klee One）、本文は丸ゴシック（Zen Maru Gothic）
- マスキングテープ、蛍光マーカー、手書き風の線・丸・チェック、少し傾いたメモ用紙のカード
- 色は 3 系統まで: 紙（ベース）・紺のインク（文字）・赤系（価格と購入導線のみ）。
  蛍光マーカーとテープは淡い色で、文字の読みやすさを邪魔しない
"""

from __future__ import annotations

import io
import math
import random
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

WIDTH, HEIGHT = 1080, 1350
MARGIN = 80

_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
FONT_CANDIDATES = {
    "hand": [  # 見出し: 鉛筆で書いたような手書き風
        str(_FONT_DIR / "KleeOne-SemiBold.ttf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
    ],
    "pop": [  # 見出し（ポップ）: 太くて丸い
        str(_FONT_DIR / "MochiyPopOne-Regular.ttf"),
        str(_FONT_DIR / "ZenMaruGothic-Bold.ttf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ],
    "bold": [  # 本文の強調: 丸ゴシック
        str(_FONT_DIR / "ZenMaruGothic-Bold.ttf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
    "regular": [  # 本文
        str(_FONT_DIR / "ZenMaruGothic-Medium.ttf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
}
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_CANDIDATES[weight]:
        if Path(path).exists():
            _font_cache[key] = ImageFont.truetype(path, size)
            return _font_cache[key]
    raise RuntimeError("日本語フォントが見つかりません。assets/fonts を確認してください")


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 配色（config/genres.json の theme で上書きできる）
# ---------------------------------------------------------------------------

class Theme:
    def __init__(self, genre: dict):
        g = genre or {}
        self.paper = _hex(g.get("paper", "#FBF6EC"))
        self.ink = _hex(g.get("ink", "#2B3A55"))        # 紺のインク（紙とのコントラスト 10.6）
        self.sub = _hex(g.get("sub", "#5B6478"))        # 補足の文字（5.5）
        self.accent = _hex(g.get("accent", "#D9472B"))  # 価格・購入導線（紙と 4.0、白文字と 4.3）
        self.marker = _hex(g.get("marker", "#FFE27A"))  # 蛍光マーカー
        self.card = _hex(g.get("card", "#FFFFFF"))
        self.dot = _hex(g.get("dot", "#E7DDC8"))
        # 背景: plain（無地の紙）/ watercolor（水彩のにじみ）/ gradient（やわらかいグラデーション）/
        #       linen（布のような織り目）/ dots（方眼ノート）
        self.background = g.get("background", "watercolor")
        # カード: soft（ぼかした影のメモ用紙）/ pop（黒い枠線と、ずらしたくっきりした影）
        self.card_style = g.get("card_style", "soft")
        # 見出しのフォント: hand（手書き風 Klee One）/ pop（太くて丸い Mochiy Pop One）
        self.heading = g.get("heading", "hand")
        self.outline = _hex(g.get("outline", g.get("ink", "#2B3A55")))
        tapes = g.get("tapes") or ["#BFE3D0", "#F7C9C4", "#FBE7A1", "#CFE0F7"]
        self.tapes = [_hex(t) for t in tapes]
        self.genre_id = g.get("id", "")
        self.pr = g.get("pr", True)  # 広告を含まない投稿（お役立ち投稿）では PR 表記を出さない
        self.label = g.get("label", "")

    def tape(self, i: int = 0) -> tuple[int, int, int]:
        base = sum(map(ord, self.genre_id)) if self.genre_id else 0
        return self.tapes[(base + i) % len(self.tapes)]


# ---------------------------------------------------------------------------
# 文字組み
# ---------------------------------------------------------------------------

NO_LINE_START = "、。，．・：；？！?!）」』】〕ーぁぃぅぇぉっゃゅょァィゥェォッャュョ…％%"


_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-./+&'%$¥,]*")
BREAK_AFTER = "、。，．！？!?）」』 　"


def _tokens(text: str) -> list[str]:
    """英数字の言葉（Wi-Fi, 150, US$2.99 など）はひとかたまり、それ以外は1文字ずつ."""
    out, i = [], 0
    while i < len(text):
        m = _ASCII_WORD.match(text, i)
        if m and m.end() - i > 1:
            out.append(m.group(0))
            i = m.end()
        else:
            out.append(text[i])
            i += 1
    return out


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    """日本語の折り返し.

    - 英数字の言葉は途中で切らない
    - 句読点などは行頭に来ないよう、前の行にぶら下げる
    - 行が長くなったら、行の後半にある読点などの「切れ目」で改行する
    """
    lines: list[str] = []
    cur, last_break = "", -1
    for tok in _tokens(text):
        if tok == "\n":
            lines.append(cur)
            cur, last_break = "", -1
        elif tok in NO_LINE_START and cur and draw.textlength(cur + tok, font=font) <= max_width * 1.06:
            cur += tok
        elif draw.textlength(cur + tok, font=font) <= max_width or not cur:
            cur += tok
        else:
            if last_break >= 0 and draw.textlength(cur[: last_break + 1], font=font) >= max_width * 0.45:
                lines.append(cur[: last_break + 1].rstrip())
                cur = cur[last_break + 1 :].lstrip() + tok
            else:
                lines.append(cur)
                cur = tok
            last_break = -1
        if cur and cur[-1] in BREAK_AFTER:
            last_break = len(cur) - 1
        if len(lines) >= max_lines:
            break
    else:
        if cur:
            lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if "".join(lines).replace(" ", "") != text.replace("\n", "").replace(" ", ""):
        last = lines[-1] if lines else ""
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1:] = [last + "…"]
    return lines


def _balance(draw, text: str, font, lines: list[str]) -> list[str]:
    """2行に折り返す場合、1行目と2行目の長さが近くなる位置で改行する."""
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


def _balance_tokens(draw, text: str, font, lines: list[str], width: int) -> list[str]:
    """2行のとき、英数字の言葉を切らない範囲で、2行の長さが近くなる位置で改行する."""
    toks = _tokens(text)
    best, best_score = lines, float("inf")
    for i in range(1, len(toks)):
        a, b = "".join(toks[:i]), "".join(toks[i:])
        if b[:1] in NO_LINE_START:
            continue
        wa, wb = draw.textlength(a, font=font), draw.textlength(b, font=font)
        if wa > width or wb > width:
            continue
        # 助詞（が・を・の・に・は・と・で・も・へ）や読点の直後で切れるなら、少し長さがずれても優先する
        soft = a[-1] in "がをのにはとでもへ、。" and b[:1] not in "っゃゅょぁぃぅぇぉー"
        score = abs(wa - wb) + (0 if soft else width * 0.25)
        if score < best_score:
            best, best_score = [a, b], score
    return best


PARTICLES = "がをのにはとでもへ"


def _natural_break(draw, text: str, font, width: int, chars: str = "、。！？!?・ 　）」』") -> list[str] | None:
    """2行にするとき、読点や記号（chars）の直後など、言葉の切れ目で改行できるならそうする."""
    best = None
    for i in range(1, len(text)):
        if text[i - 1] not in chars or text[i : i + 1] in "っゃゅょぁぃぅぇぉー":
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
    target = draw.textlength(text, font=font) / n
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


def _fit_lines(draw, text: str, weight: str, sizes: range, width: int, max_lines: int):
    """できるだけ大きく、行数は少なく、最後の行が1〜2文字だけにならない組み方を探す."""
    one = _font(weight, sizes[0])
    if max_lines >= 2 and draw.textlength(text, font=one) > width:
        for size in sizes:  # 2行になるなら、言葉の切れ目で改行できる最大の大きさを優先
            if size < sizes[0] * 0.6:
                break
            f = _font(weight, size)
            if draw.textlength(text, font=f) <= width:
                break
            nb = _natural_break(draw, text, f, width)
            if nb is None and size >= sizes[0] * 0.75:
                nb = _natural_break(draw, text, f, width, PARTICLES)
            if nb:
                return f, nb
    for limit in range(1, max_lines + 1):
        for size in sizes:
            f = _font(weight, size)
            lines = _wrap(draw, text, f, width, limit + 1)
            if len(lines) > limit or "…" in lines[-1]:
                continue
            if len(lines) == 2:
                lines = _natural_break(draw, text, f, width) or _balance_tokens(draw, text, f, lines, width)
            if len(lines) > 1 and len(lines[-1]) <= 2:
                continue
            if size >= sizes[0] * 0.7 or limit == max_lines:
                return f, lines
    f = _font(weight, sizes[-1])
    return f, _wrap(draw, text, f, width, max_lines)


# ---------------------------------------------------------------------------
# 手作り感のある装飾
# ---------------------------------------------------------------------------

def _paper(theme: Theme, seed: int, size=(WIDTH, HEIGHT)) -> Image.Image:
    """背景の紙。theme.background で種類を切り替える（どれも最後に紙の粒子を薄くのせる）."""
    w, h = size
    style = theme.background
    img = Image.new("RGB", size, theme.paper)
    if style == "dots":
        draw = ImageDraw.Draw(img)
        for y in range(20, h, 40):
            for x in range(20, w, 40):
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=theme.dot)
    elif style == "watercolor":
        # 四隅に淡い水彩のにじみ（位置は投稿番号ごとに少し変わる）
        r = _rng("wc", seed)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        corners = [(0, 0), (w, 0), (0, h), (w, h)]
        r.shuffle(corners)
        for i, (cx, cy) in enumerate(corners[:3]):
            color = theme.tapes[(i + seed) % len(theme.tapes)]
            rad = r.randint(int(w * 0.32), int(w * 0.48))
            for k in range(5):  # 少しずつずらして重ね、にじみのムラを出す
                ox, oy = r.randint(-60, 60), r.randint(-60, 60)
                d.ellipse((cx - rad + ox, cy - rad + oy, cx + rad + ox, cy + rad + oy), fill=(*color, 70))
        layer = layer.filter(ImageFilter.GaussianBlur(55))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    elif style == "pop":
        # 無地の明るい色に、ふちの方だけ小さな丸・星・波線を散らす（中央の文字にはかからない）
        r = _rng("pop", seed)
        draw = ImageDraw.Draw(img)
        colors = theme.tapes + [(255, 255, 255)]
        spots = []
        for _ in range(26):
            side = r.choice(["top", "bottom", "left", "right"])
            if side in ("top", "bottom"):
                x = r.randint(20, w - 20)
                y = r.randint(10, 130) if side == "top" else r.randint(h - 130, h - 10)
            else:
                x = r.randint(8, 56) if side == "left" else r.randint(w - 56, w - 8)
                y = r.randint(150, h - 150)
            if any(abs(x - a) < 60 and abs(y - b) < 60 for a, b in spots):
                continue
            spots.append((x, y))
            c = r.choice(colors)
            kind = r.choice(["dot", "dot", "ring", "star", "squiggle"])
            k = r.randint(8, 16)
            if kind == "dot":
                draw.ellipse((x - k, y - k, x + k, y + k), fill=c)
            elif kind == "ring":
                draw.ellipse((x - k, y - k, x + k, y + k), outline=c, width=5)
            elif kind == "star":
                draw.line([(x - k, y), (x + k, y)], fill=c, width=5)
                draw.line([(x, y - k), (x, y + k)], fill=c, width=5)
            else:
                pts = [(x - 24 + i * 6, y + (6 if i % 2 else -6)) for i in range(9)]
                draw.line(pts, fill=c, width=5, joint="curve")
    elif style == "gradient":
        top, bottom = theme.paper, _mix(theme.paper, theme.tapes[seed % len(theme.tapes)], 0.45)
        grad = Image.new("RGB", (1, h))
        for y in range(h):
            grad.putpixel((0, y), _mix(top, bottom, y / max(1, h - 1)))
        img = grad.resize(size)
    elif style == "linen":
        draw = ImageDraw.Draw(img)
        line = _mix(theme.paper, theme.dot, 0.55)
        for x in range(0, w, 6):
            draw.line([(x, 0), (x, h)], fill=line, width=1)
        for y in range(0, h, 6):
            draw.line([(0, y), (w, y)], fill=line, width=1)
        img = Image.blend(Image.new("RGB", size, theme.paper), img, 0.5)
    noise = Image.effect_noise(size, 18).convert("L")
    grain = Image.merge("RGB", [noise] * 3)
    return Image.blend(img, grain, 0.035)


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rng(*parts) -> random.Random:
    return random.Random("|".join(map(str, parts)))


def _wobble(draw, x0: float, y0: float, x1: float, y1: float, color, width: int, seed, amp: float = 3.0):
    """手で引いたような、少し揺れる線."""
    r = _rng("w", seed, x0, y0)
    length = math.hypot(x1 - x0, y1 - y0)
    n = max(2, int(length / 18))
    nx, ny = -(y1 - y0) / (length or 1), (x1 - x0) / (length or 1)
    pts = []
    for i in range(n + 1):
        t = i / n
        off = r.uniform(-amp, amp) * math.sin(math.pi * t) + r.uniform(-amp, amp) * 0.4
        pts.append((x0 + (x1 - x0) * t + nx * off, y0 + (y1 - y0) * t + ny * off))
    draw.line(pts, fill=color, width=width, joint="curve")


def _hand_circle(draw, cx: float, cy: float, rx: float, ry: float, color, width: int, seed):
    """少しずれて1周ちょっと描いたような、手書きの丸."""
    r = _rng("c", seed, cx, cy)
    pts = []
    start = r.uniform(0, math.tau)
    for i in range(0, 76):
        a = start + i / 70 * math.tau
        k = 1 + r.uniform(-0.03, 0.03) + 0.05 * (i / 76)
        pts.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))
    draw.line(pts, fill=color, width=width, joint="curve")


def _check(draw, x: float, y: float, size: float, color, width: int = 7):
    """手書きのチェックマーク（左上 x, y）."""
    draw.line([(x, y + size * 0.55), (x + size * 0.38, y + size * 0.9), (x + size, y + size * 0.05)],
              fill=color, width=width, joint="curve")


def _marker(base: Image.Image, x0: int, y0: int, x1: int, y1: int, color, alpha: int = 150, seed=0):
    """蛍光マーカーを引いたような、少し斜めの帯."""
    r = _rng("m", seed, x0, y0)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tilt = r.uniform(-4, 4)
    d.polygon([(x0 - 6, y0 + tilt), (x1 + 4, y0 - tilt), (x1 + 8, y1 - tilt), (x0 - 2, y1 + tilt)],
              fill=(*color, alpha))
    if base.mode == "RGBA":
        base.alpha_composite(layer)
    else:
        base.paste(Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB"))


def _tape(base: Image.Image, cx: int, cy: int, w: int, h: int, color, angle: float, text: str = "",
          font=None, text_color=(43, 58, 85)):
    """マスキングテープ（半透明・端がギザギザ）。文字を書くこともできる."""
    layer = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    zig = 6
    left = [(20 + (zig if i % 2 else 0), 20 + i * h / 8) for i in range(9)]
    right = [(20 + w - (zig if i % 2 else 0), 20 + h - i * h / 8) for i in range(9)]
    d.polygon(left + right, fill=(*color, 215))
    for i in range(0, w, 14):  # テープのうっすらした筋
        d.line([(20 + i, 22), (20 + i, 18 + h)], fill=(255, 255, 255, 18), width=2)
    if text:
        d.text((20 + w / 2, 20 + h / 2), text, font=font, fill=text_color, anchor="mm")
    rotated = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    base.paste(rotated, (int(cx - rotated.width / 2), int(cy - rotated.height / 2)), rotated)


def _card(base: Image.Image, box: tuple[int, int, int, int], theme: Theme, angle: float = 0.0,
          radius: int = 26, fill=None):
    """メモ用紙のようなカード。返した layer に中身を描いてから commit() で影付き・少し傾けて貼る."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pop = theme.card_style == "pop"
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=(*(fill or theme.card), 255),
                        outline=(*theme.outline, 255) if pop else None, width=5 if pop else 0)

    def commit():
        pad = 40
        holder = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        shadow = Image.new("RGBA", holder.size, (0, 0, 0, 0))
        if pop:  # ずらした、くっきりした影（ポップな印象）
            ImageDraw.Draw(shadow).rounded_rectangle((pad + 12, pad + 14, pad + w + 12, pad + h + 14), radius=radius,
                                                     fill=(*theme.outline, 255))
            holder.alpha_composite(shadow)
        else:
            ImageDraw.Draw(shadow).rounded_rectangle((pad + 4, pad + 10, pad + w + 4, pad + h + 10), radius=radius,
                                                     fill=(60, 50, 40, 38))
            holder.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
        holder.alpha_composite(layer, (pad, pad))
        rotated = holder.rotate(angle, resample=Image.BICUBIC, expand=True)
        cx, cy = x0 + w / 2, y0 + h / 2
        base.paste(rotated, (int(cx - rotated.width / 2), int(cy - rotated.height / 2)), rotated)

    return layer, d, commit


def _canvas(genre: dict, number: int, *, number_label: str | None = None):
    theme = Theme(genre)
    img = _paper(theme, number)
    if theme.pr:
        _tape(img, MARGIN + 60, 78, 130, 54, theme.tape(0), -4, "PR", _font("bold", 32), theme.ink)
    draw = ImageDraw.Draw(img)
    if theme.label:
        draw.text((MARGIN + (150 if theme.pr else 0), 80), theme.label, font=_font("hand", 34), fill=theme.sub,
                  anchor="lm")
    draw.text((WIDTH - MARGIN, 80), number_label or f"No.{number:03d}", font=_font("hand", 40), fill=theme.ink,
              anchor="rm")
    return img, draw, theme


def _title(img, theme: Theme, text: str, y: int, seed, size_range=range(70, 47, -4)) -> int:
    """手書き風の見出し＋蛍光マーカー。次に描ける y を返す."""
    draw = ImageDraw.Draw(img)
    font, lines = _fit_lines(draw, text, theme.heading, size_range, WIDTH - MARGIN * 2, 2)
    lh = int(font.size * 1.35)
    yy = y
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        _marker(img, MARGIN, yy + int(font.size * 0.62), MARGIN + int(w), yy + int(font.size * 1.12), theme.marker,
                seed=f"{seed}{i}")
        yy += lh
    draw = ImageDraw.Draw(img)
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=theme.ink)
        y += lh
    return y + 36


def _footer(img, theme: Theme, *, swipe: bool = True, note: str = "", account: str = ""):
    draw = ImageDraw.Draw(img)
    if note:
        y = HEIGHT - 96 if swipe else HEIGHT - 44
        draw.text((MARGIN, y), note, font=_font("regular", 24), fill=theme.sub, anchor="ls")
    elif account:
        draw.text((MARGIN, HEIGHT - 44), f"@{account}", font=_font("regular", 28), fill=theme.sub, anchor="ls")
    if swipe:
        x = WIDTH - MARGIN - 60
        draw.text((x, HEIGHT - 44), "スワイプしてね", font=_font("hand", 34), fill=theme.accent, anchor="rs")
        _wobble(draw, x + 8, HEIGHT - 58, x + 52, HEIGHT - 58, theme.accent, 5, "sw", amp=1.5)
        draw.line([(x + 36, HEIGHT - 70), (x + 54, HEIGHT - 58), (x + 36, HEIGHT - 46)], fill=theme.accent,
                  width=5, joint="curve")


def _cta_button(img, theme: Theme, y: int, text: str, height: int = 104) -> int:
    x0, x1 = MARGIN, WIDTH - MARGIN
    if theme.card_style == "pop":
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((x0 + 10, y + 12, x1 + 10, y + height + 12), radius=height // 2, fill=theme.outline)
        draw.rounded_rectangle((x0, y, x1, y + height), radius=height // 2, fill=theme.accent,
                               outline=theme.outline, width=5)
    else:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x0 + 6, y + 8, x1 + 6, y + height + 8), radius=height // 2,
                                                 fill=(*theme.ink, 60))
        img.paste(Image.alpha_composite(img.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(4))).convert("RGB"))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((x0, y, x1, y + height), radius=height // 2, fill=theme.accent)
    f, lines = _fit_lines(draw, text, "bold", range(42, 29, -2), x1 - x0 - 60, 1)
    draw.text(((x0 + x1) // 2, y + height // 2), lines[0], font=f, fill=(255, 255, 255), anchor="mm")
    return y + height


def _save(img: Image.Image, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path


def _fetch_image(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def _polaroid(img: Image.Image, theme: Theme, picture: Image.Image, cx: int, top: int, max_w: int, max_h: int,
              angle: float, caption: str = "") -> int:
    """写真や公式画像を、白いフチ付きの写真のように貼る（画像自体は縮小のみ・切り抜きなし）."""
    scale = min(max_w / picture.width, max_h / picture.height, 3.0)
    pic = picture.resize((max(1, int(picture.width * scale)), max(1, int(picture.height * scale))), Image.LANCZOS)
    pad, bottom = 24, (70 if caption else 24)
    w, h = pic.width + pad * 2, pic.height + pad + bottom
    box = (cx - w // 2, top, cx - w // 2 + w, top + h)
    layer, d, commit = _card(img, box, theme, angle=angle, radius=10)
    layer.paste(pic, (pad, pad))
    if caption:
        d.text((w // 2, pic.height + pad + bottom // 2), caption, font=_font("hand", 28), fill=theme.sub,
               anchor="mm")
    commit()
    _tape(img, box[0] + 70, top + 6, 150, 46, theme.tape(1), -12)
    _tape(img, box[2] - 70, top + 6, 150, 46, theme.tape(2), 10)
    return top + h


def _price_line(img, theme: Theme, y: int, label: str, review_average: float = 0, review_count: int = 0,
                seed="price") -> None:
    draw = ImageDraw.Draw(img)
    width = int((WIDTH - MARGIN * 2) * (0.58 if review_count else 1))
    lf, ll = _fit_lines(draw, label, "bold", range(66, 37, -2), width, 1)
    lw = draw.textlength(ll[0], font=lf)
    _marker(img, MARGIN, y - int(lf.size * 0.05), MARGIN + int(lw), y + int(lf.size * 0.42), theme.marker, seed=seed)
    draw = ImageDraw.Draw(img)
    draw.text((MARGIN, y), ll[0], font=lf, fill=theme.accent, anchor="lm")
    if review_count:
        draw.text((WIDTH - MARGIN, y), f"★{review_average:.1f}（{review_count:,}件）", font=_font("regular", 34),
                  fill=theme.ink, anchor="rm")


def _checklist_card(img, theme: Theme, box, items: list[str], angle: float, font_size: int, max_lines: int = 1):
    x0, y0, x1, y1 = box
    layer, d, commit = _card(img, box, theme, angle=angle)
    w, h = x1 - x0, y1 - y0
    pf = _font("bold", font_size)
    row = h // max(1, len(items))
    for i, pt in enumerate(items):
        cy = row * i + row // 2
        _check(d, 36, cy - 24, 44, theme.accent)
        lines = _wrap(d, pt, pf, w - 150, max_lines)
        lh = int(pf.size * 1.3)
        ty = cy - lh * len(lines) // 2
        for line in lines:
            d.text((104, ty), line, font=pf, fill=theme.ink)
            ty += lh
        if i < len(items) - 1:
            _wobble(d, 36, row * (i + 1), w - 36, row * (i + 1), (*theme.dot, 255), 3, f"cl{i}", 1.5)
    commit()


# ---------------------------------------------------------------------------
# 単品カード（楽天の自動選定・写真カルーセルの最後の1枚など）
# ---------------------------------------------------------------------------

def render_card(*, out_path: Path, number: int, genre: dict, headline: str, short_name: str, points: list[str],
                price: int, review_average: float, review_count: int, product_image_url: str | None = None,
                account_name: str = "", price_label: str | None = None, cta_verb: str = "購入は",
                footnote: str = "※価格は投稿時点のものです") -> Path:
    img, draw, theme = _canvas(genre, number)
    product = _fetch_image(product_image_url) if product_image_url else None
    y = _title(img, theme, headline, 170, f"card{number}", range(84, 55, -4))
    draw = ImageDraw.Draw(img)
    name_f, name_l = _fit_lines(draw, short_name, "regular", range(38, 27, -2), WIDTH - MARGIN * 2, 1)
    draw.text((MARGIN, y - 10), name_l[0], font=name_f, fill=theme.sub)
    y += 60
    if product is not None:
        y = _polaroid(img, theme, product, WIDTH // 2, y + 10, 560, 340, -1.5) + 30
    footer_top = HEIGHT - 290
    card_h = min(560 if product is None else 380, footer_top - 40 - y)
    y += max(0, (footer_top - 40 - y - card_h) // 2)
    _checklist_card(img, theme, (MARGIN, y, WIDTH - MARGIN, y + card_h), points[:3], -0.6,
                    50 if product is None else 40)
    label = price_label if price_label is not None else f"¥{price:,}"
    if label:
        _price_line(img, theme, footer_top + 50, label, review_average, review_count)
    _cta_button(img, theme, footer_top + 110, f"{cta_verb}プロフィールのリンク → #{number:03d}")
    _footer(img, theme, swipe=False, note=footnote)
    if account_name:
        ImageDraw.Draw(img).text((WIDTH - MARGIN, HEIGHT - 44), f"@{account_name}", font=_font("regular", 26),
                                 fill=theme.sub, anchor="rs")
    return _save(img, out_path)


# ---------------------------------------------------------------------------
# 自分で撮った写真を使うスライド
# ---------------------------------------------------------------------------

def _load_photo(path: Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # スマホ写真の向きを補正
    return img.convert("RGB")


def _cover_crop(img: Image.Image) -> Image.Image:
    """4:5 に中央トリミングして 1080x1350 にする."""
    return ImageOps.fit(img, (WIDTH, HEIGHT), method=Image.LANCZOS, centering=(0.5, 0.45))


def render_photo_cover(*, photo: Path, out_path: Path, number: int, genre: dict, headline: str,
                       short_name: str) -> Path:
    """1枚目: 自分の写真の上に、テープで留めたメモ用紙の見出し."""
    theme = Theme(genre)
    img = _cover_crop(_load_photo(photo))
    _tape(img, MARGIN + 60, 78, 130, 54, theme.tape(0), -4, "PR", _font("bold", 32), theme.ink)
    draw = ImageDraw.Draw(img)
    font, lines = _fit_lines(draw, headline, theme.heading, range(78, 51, -3), WIDTH - MARGIN * 2 - 80, 2)
    lh = int(font.size * 1.35)
    h = 60 + lh * len(lines) + 110
    top = HEIGHT - 70 - h
    layer, d, commit = _card(img, (MARGIN - 10, top, WIDTH - MARGIN + 10, top + h), theme, angle=-1.2,
                             fill=theme.paper)
    y = 50
    for line in lines:
        d.text((50, y), line, font=font, fill=theme.ink)
        y += lh
    d.text((50, y + 4), short_name, font=_font("regular", 32), fill=theme.sub)
    d.text((WIDTH - MARGIN * 2 - 20, h - 26), f"No.{number:03d}  スワイプ →", font=_font("hand", 30),
           fill=theme.accent, anchor="rs")
    commit()
    _tape(img, WIDTH // 2, top + 4, 200, 52, theme.tape(1), 3)
    return _save(img, out_path)


def render_photo_slide(*, photo: Path, out_path: Path, caption: str = "", genre: dict | None = None) -> Path:
    """2枚目以降: 写真＋（任意で）テープで貼った一言メモ."""
    theme = Theme(genre or {})
    img = _cover_crop(_load_photo(photo))
    if caption:
        draw = ImageDraw.Draw(img)
        f, lines = _fit_lines(draw, caption, theme.heading, range(54, 37, -2), WIDTH - MARGIN * 2 - 160, 2)
        lh = int(f.size * 1.35)
        h = 50 + lh * len(lines) + 40
        top = HEIGHT - 80 - h
        layer, d, commit = _card(img, (MARGIN, top, WIDTH - MARGIN - 60, top + h), theme, angle=1.0,
                                 fill=theme.paper)
        y = 40
        for line in lines:
            d.text((40, y), line, font=f, fill=theme.ink)
            y += lh
        commit()
        _tape(img, MARGIN + 90, top + 2, 150, 44, theme.tape(2), -8)
    return _save(img, out_path)


# ---------------------------------------------------------------------------
# まとめ・比較投稿のスライド
# ---------------------------------------------------------------------------

def render_roundup_cover(*, out_path: Path, number: int, genre: dict, title: str, subtitle: str,
                         names: list[str], account_name: str = "") -> Path:
    img, draw, theme = _canvas(genre, number)
    _tape(img, MARGIN + 130, 210, 250, 64, theme.tape(2), -3, f"保存版 {len(names)}選", _font("bold", 34), theme.ink)
    y = _title(img, theme, title, 280, f"rc{number}", range(96, 59, -4))
    draw = ImageDraw.Draw(img)
    sf, sl = _fit_lines(draw, subtitle, "regular", range(40, 29, -2), WIDTH - MARGIN * 2, 2)
    for line in sl:
        draw.text((MARGIN, y - 10), line, font=sf, fill=theme.sub)
        y += int(sf.size * 1.45)
    y += 30
    row = min(84, (HEIGHT - 140 - y - 60) // max(1, len(names)))
    card_h = row * len(names) + 60
    layer, d, commit = _card(img, (MARGIN, y, WIDTH - MARGIN, y + card_h), theme, angle=0.8)
    lf = _font("bold", max(28, min(38, row - 34)))
    for i, name in enumerate(names):
        cy = 30 + row * i + row // 2
        _hand_circle(d, 62, cy, 26, 24, theme.accent, 4, f"rc{i}")
        d.text((62, cy), str(i + 1), font=_font("hand", 34), fill=theme.accent, anchor="mm")
        t = _wrap(d, name, lf, WIDTH - MARGIN * 2 - 150, 1)
        d.text((112, cy), t[0] if t else "", font=lf, fill=theme.ink, anchor="lm")
    commit()
    _footer(img, theme, account=account_name)
    return _save(img, out_path)


def render_roundup_item(*, out_path: Path, number: int, genre: dict, index: int, total: int,
                        short_name: str, best_for: str, points: list[str], price: int,
                        review_average: float, review_count: int,
                        product_image_url: str | None = None) -> Path:
    img, draw, theme = _canvas(genre, number, number_label=f"{index} / {total}")
    _hand_circle(draw, MARGIN + 55, 235, 52, 48, theme.accent, 6, f"ri{index}")
    draw.text((MARGIN + 55, 235), str(index), font=_font("hand", 64), fill=theme.accent, anchor="mm")
    nf, nl = _fit_lines(draw, short_name, theme.heading, range(66, 41, -3), WIDTH - MARGIN * 2 - 150, 2)
    lh = int(nf.size * 1.3)
    ny = 235 - lh * len(nl) // 2
    for line in nl:
        draw.text((MARGIN + 140, ny), line, font=nf, fill=theme.ink)
        ny += lh
    y = 340
    if best_for:
        bf = _font("bold", 36)
        t = _wrap(draw, best_for, bf, WIDTH - MARGIN * 2 - 80, 1)[0]
        tw = int(draw.textlength(t, font=bf))
        _tape(img, MARGIN + tw // 2 + 40, y + 40, tw + 80, 66, theme.tape(index), -1.5, t, bf, theme.ink)
        y += 110
    product = _fetch_image(product_image_url) if product_image_url else None
    if product is not None:
        y = _polaroid(img, theme, product, WIDTH // 2, y + 10, 520, 320, 1.2) + 30
    footer_top = HEIGHT - 290
    card_h = min(480 if product is None else 330, footer_top - 40 - y)
    y += max(0, (footer_top - 40 - y - card_h) // 2)
    _checklist_card(img, theme, (MARGIN, y, WIDTH - MARGIN, y + card_h), points[:2], -0.7,
                    50 if product is None else 38, max_lines=2)
    _price_line(img, theme, footer_top + 50, f"¥{price:,}", review_average, review_count, seed=f"p{index}")
    _cta_button(img, theme, footer_top + 110, f"購入はプロフィールのリンク → #{number:03d}-{index}")
    _footer(img, theme, swipe=False, note="※価格は投稿時点のものです")
    return _save(img, out_path)


def render_roundup_summary(*, out_path: Path, number: int, genre: dict, rows: list[dict]) -> Path:
    """最後のスライド: 価格・評価の比較表（保存されやすい）."""
    img, draw, theme = _canvas(genre, number)
    y = _title(img, theme, "ひと目で比較", 170, f"rs{number}", range(78, 59, -4))
    draw = ImageDraw.Draw(img)
    draw.text((MARGIN, y - 16), "保存しておくと、買い物のときに便利です", font=_font("hand", 34), fill=theme.sub)
    y += 50
    footer_top = HEIGHT - 250
    row_h = min(150, (footer_top - 40 - y) // max(1, len(rows)))
    card_h = row_h * len(rows) + 30
    layer, d, commit = _card(img, (MARGIN, y, WIDTH - MARGIN, y + card_h), theme, angle=0.5)
    cw = WIDTH - MARGIN * 2
    nf = _font("bold", min(38, row_h // 3))
    sf = _font("regular", min(28, row_h // 4))
    pf = _font("bold", min(44, row_h // 3))
    for i, r in enumerate(rows):
        top = 15 + row_h * i
        cy = top + row_h // 2
        _hand_circle(d, 52, cy, 24, 22, theme.accent, 4, f"rs{i}")
        d.text((52, cy), str(i + 1), font=_font("hand", 30), fill=theme.accent, anchor="mm")
        name = _wrap(d, r["short_name"], nf, cw - 330, 1)
        d.text((96, cy - 4), name[0] if name else "", font=nf, fill=theme.ink, anchor="ls")
        if r.get("best_for"):
            bf = _wrap(d, r["best_for"], sf, cw - 330, 1)
            d.text((96, cy + 10), bf[0] if bf else "", font=sf, fill=theme.sub, anchor="lt")
        d.text((cw - 30, cy - 4), f"¥{r['price']:,}", font=pf, fill=theme.accent, anchor="rs")
        if r.get("review_count"):
            d.text((cw - 30, cy + 10), f"★{r['review_average']:.1f}（{r['review_count']:,}）", font=sf,
                   fill=theme.sub, anchor="rt")
        if i < len(rows) - 1:
            _wobble(d, 30, top + row_h, cw - 30, top + row_h, (*theme.dot, 255), 3, f"rsl{i}", 1.5)
    commit()
    _cta_button(img, theme, footer_top + 80, f"購入はプロフィールのリンク → #{number:03d}")
    _footer(img, theme, swipe=False, note="※価格・評価は投稿時点のものです")
    return _save(img, out_path)


# ---------------------------------------------------------------------------
# 図解カルーセル（写真がない A8 案件など）
# ---------------------------------------------------------------------------

def render_explainer_cover(*, out_path: Path, number: int, genre: dict, hook: str, sub: str,
                           account_name: str = "") -> Path:
    img, draw, theme = _canvas(genre, number)
    _tape(img, MARGIN + 150, 250, 290, 66, theme.tape(2), -3, "保存して見返してね", _font("bold", 32), theme.ink)
    w = WIDTH - MARGIN * 2
    font, lines = _fit_lines(draw, hook, theme.heading, range(124, 83, -4), w, 3)
    lh = int(font.size * 1.32)
    sub_font, sub_lines = _fit_lines(draw, sub, "regular", range(44, 31, -2), w, 2)
    block = lh * len(lines) + 40 + int(sub_font.size * 1.5) * len(sub_lines)
    y = max(380, (HEIGHT - block) // 2 - 10)
    yy = y
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        _marker(img, MARGIN, yy + int(font.size * 0.6), MARGIN + int(lw), yy + int(font.size * 1.12), theme.marker,
                seed=f"cv{i}")
        yy += lh
    draw = ImageDraw.Draw(img)
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=theme.ink)
        y += lh
    y += 40
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=sub_font, fill=theme.sub)
        y += int(sub_font.size * 1.5)
    for sx, sy, s in [(WIDTH - 150, 255, 26), (WIDTH - 105, 305, 16)]:  # きらっと光る落書き
        draw.line([(sx - s, sy), (sx + s, sy)], fill=theme.accent, width=5)
        draw.line([(sx, sy - s), (sx, sy + s)], fill=theme.accent, width=5)
    _footer(img, theme, account=account_name)
    return _save(img, out_path)


def render_list_slide(*, out_path: Path, number: int, genre: dict, title: str, items: list[str],
                      mark: str = "check", footnote: str = "") -> Path:
    """箇条書きスライド。mark='pain' は困りごと（手書きの丸に「?」）、'check' はポイント（✓）."""
    img, draw, theme = _canvas(genre, number)
    y = _title(img, theme, title, 170, f"ls{number}{title}")
    avail = HEIGHT - 140 - y
    n = max(1, len(items))
    gap = 38
    card_h = min(230, (avail - gap * (n - 1)) // n)
    y += max(0, (avail - (card_h * n + gap * (n - 1))) // 2)
    f = _font("bold", 48 if card_h >= 180 else 42)
    angles = [-1.2, 0.9, -0.6, 1.1]
    for i, item in enumerate(items):
        layer, d, commit = _card(img, (MARGIN, y, WIDTH - MARGIN, y + card_h), theme, angle=angles[i % 4])
        cy = card_h // 2
        if mark == "pain":
            _hand_circle(d, 70, cy, 36, 34, theme.accent, 5, f"p{i}")
            d.text((70, cy + 2), "?", font=_font("hand", 48), fill=theme.accent, anchor="mm")
        else:
            _check(d, 42, cy - 26, 52, theme.accent, 8)
        lines = _wrap(d, item, f, WIDTH - MARGIN * 2 - 170, 2)
        lh = int(f.size * 1.35)
        ty = cy - lh * len(lines) // 2
        for line in lines:
            d.text((134, ty), line, font=f, fill=theme.ink)
            ty += lh
        commit()
        y += card_h + gap
    _footer(img, theme, note=footnote)
    return _save(img, out_path)


def render_compare_slide(*, out_path: Path, number: int, genre: dict, title: str, columns: list[str],
                         rows: list[tuple[str, list[str]]]) -> Path:
    """比較表。最後の列（紹介するサービスの種類）をマーカーと手書きの丸で強調する."""
    img, draw, theme = _canvas(genre, number)
    y = _title(img, theme, title, 170, f"cp{number}")
    head_h = 120
    row_h = min(170, (HEIGHT - 190 - y - head_h) // max(1, len(rows)))
    card_h = head_h + row_h * len(rows) + 30
    y += max(0, (HEIGHT - 170 - y - card_h) // 2)
    cw = WIDTH - MARGIN * 2
    layer, d, commit = _card(img, (MARGIN, y, WIDTH - MARGIN, y + card_h), theme, angle=0.4)
    label_w = 270
    col_w = (cw - label_w - 20) // len(columns)
    hi = len(columns) - 1
    hx = label_w + col_w * hi
    # マーカー色がカードの色に近いとき（白いマーカーなど）は、アクセント色をうすくしたもので強調する
    near = sum(abs(a - b) for a, b in zip(theme.marker, theme.card)) < 90
    col_hl = _mix(theme.card, theme.accent, 0.16) if near else theme.marker
    _marker(layer, hx + 8, 14, hx + col_w - 8, card_h - 14, col_hl, alpha=170 if near else 110, seed="cpcol")
    d = ImageDraw.Draw(layer)
    for i, col in enumerate(columns):
        cx = label_w + col_w * i + col_w // 2
        hf, lines = _fit_lines(d, col, "bold", range(36, 23, -2), col_w - 24, 2)
        lh = int(hf.size * 1.25)
        ty = 15 + head_h // 2 - lh * len(lines) // 2
        for line in lines:
            d.text((cx, ty), line, font=hf, fill=theme.accent if i == hi else theme.ink, anchor="mt")
            ty += lh
    _hand_circle(d, hx + col_w // 2, 15 + head_h // 2, col_w // 2 - 6, 42, theme.accent, 4, "cphead")
    yy = 15 + head_h
    vf, vs = _font("bold", 52), _font("bold", 32)
    for r_i, (label, values) in enumerate(rows):
        _wobble(d, 24, yy, cw - 24, yy, (*theme.dot, 255), 3, f"cpr{r_i}", 1.5)
        cy = yy + row_h // 2
        lf, ll = _fit_lines(d, label, "bold", range(34, 23, -2), label_w - 40, 2)
        lh = int(lf.size * 1.25)
        ty = cy - lh * len(ll) // 2
        for line in ll:
            d.text((30, ty), line, font=lf, fill=theme.sub)
            ty += lh
        for i, v in enumerate(values):
            cx = label_w + col_w * i + col_w // 2
            font = vf if len(v) <= 1 else vs
            t = _wrap(d, v, font, col_w - 24, 1)
            d.text((cx, cy), t[0] if t else "", font=font, fill=theme.accent if i == hi else theme.ink, anchor="mm")
        yy += row_h
    commit()
    _footer(img, theme, note="※一般的な傾向です。条件は各サービスでご確認ください")
    return _save(img, out_path)


def render_steps_slide(*, out_path: Path, number: int, genre: dict, title: str,
                       steps: list[tuple[str, str]]) -> Path:
    img, draw, theme = _canvas(genre, number)
    y = _title(img, theme, title, 170, f"st{number}")
    n = max(1, len(steps))
    avail = HEIGHT - 150 - y
    step_h = min(250, avail // n)
    y += max(0, (avail - step_h * n) // 2)
    tf, df = _font("bold", 52), _font("regular", 38)
    cx = MARGIN + 50
    for i, (t, desc) in enumerate(steps, start=1):
        draw = ImageDraw.Draw(img)
        cy = y + 50
        if i < n:  # 次の手順まで点線
            for k in range(int(cy + 62), int(y + step_h - 4), 22):
                draw.line([(cx, k), (cx, k + 10)], fill=theme.sub, width=4)
        draw.ellipse((cx - 48, cy - 48, cx + 48, cy + 48), fill=theme.accent)
        draw.text((cx, cy + 2), str(i), font=_font("hand", 58), fill=(255, 255, 255), anchor="mm")
        tl = _wrap(draw, t, tf, WIDTH - MARGIN * 2 - 150, 1)
        tw = draw.textlength(tl[0] if tl else "", font=tf)
        _marker(img, MARGIN + 140, y + 34, MARGIN + 140 + int(tw), y + 70, theme.marker, seed=f"st{i}")
        draw = ImageDraw.Draw(img)
        draw.text((MARGIN + 140, y + 8), tl[0] if tl else "", font=tf, fill=theme.ink)
        dy = y + 90
        for line in _wrap(draw, desc, df, WIDTH - MARGIN * 2 - 150, 2):
            draw.text((MARGIN + 140, dy), line, font=df, fill=theme.sub)
            dy += 54
        y += step_h
    _footer(img, theme, note="※一般的な流れです。詳しい手順は公式サイトでご確認ください")
    return _save(img, out_path)


def render_official_image_slide(*, out_path: Path, number: int, genre: dict, title: str,
                                image_url: str) -> Path | None:
    """広告主の公式画像素材を、切り抜き・加工せずに縮小だけして、写真のように貼る。取得できなければ None."""
    official = _fetch_image(image_url)
    if official is None:
        return None
    img, draw, theme = _canvas(genre, number)
    y = _title(img, theme, title, 170, f"oi{number}")
    _polaroid(img, theme, official, WIDTH // 2, y + 30, WIDTH - MARGIN * 2 - 80, HEIGHT - y - 260, -1.8,
              caption="公式の広告素材より")
    _footer(img, theme)
    return _save(img, out_path)


def render_cta_slide(*, out_path: Path, number: int, genre: dict, short_name: str, price_label: str,
                     account_name: str = "") -> Path:
    img, draw, theme = _canvas(genre, number)
    w = WIDTH - MARGIN * 2
    draw.text((WIDTH // 2, 290), "気になったら、チェックしてみてね", font=_font("hand", 46), fill=theme.sub,
              anchor="mm")
    font, lines = _fit_lines(draw, short_name, theme.heading, range(110, 63, -4), w, 2)
    y = yy = 380
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        _marker(img, (WIDTH - int(lw)) // 2, yy + int(font.size * 0.62), (WIDTH + int(lw)) // 2,
                yy + int(font.size * 1.12), theme.marker, seed=f"cta{i}")
        yy += int(font.size * 1.35)
    draw = ImageDraw.Draw(img)
    for line in lines:
        draw.text((WIDTH // 2, y), line, font=font, fill=theme.ink, anchor="mt")
        y += int(font.size * 1.35)
    if price_label:
        pf, pl = _fit_lines(draw, price_label, "bold", range(70, 41, -4), w, 1)
        draw.text((WIDTH // 2, y + 40), pl[0], font=pf, fill=theme.accent, anchor="mt")
        y += 40 + int(pf.size * 1.4)
    by = max(y + 70, 820)
    _cta_button(img, theme, by, f"プロフィールのリンク → #{number:03d}", height=116)
    draw = ImageDraw.Draw(img)
    draw.text((WIDTH // 2, by + 180), "保存しておくと、あとで見返せます", font=_font("hand", 38), fill=theme.sub,
              anchor="mt")
    _footer(img, theme, swipe=False, note="※条件・料金は公式サイトでご確認ください")
    if account_name:
        ImageDraw.Draw(img).text((WIDTH - MARGIN, HEIGHT - 44), f"@{account_name}", font=_font("regular", 26),
                                 fill=theme.sub, anchor="rs")
    return _save(img, out_path)
