"""カルーセルの画像をつないで、Instagram リール用の縦長動画（1080x1920, 9:16）を作る."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .image_card import HEIGHT as SLIDE_H, WIDTH as SLIDE_W, Theme, _font, _marker, _paper, _tape

REEL_W, REEL_H = 1080, 1920
FPS = 30
BGM_FILE = config.ROOT / "assets" / "bgm.mp3"  # 任意。置けばBGMとして使う（著作権フリーの音源のみ）


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


SCALE = 0.86  # Instagram の画面（下のキャプション・右のボタン）にかからないよう、少し小さくして上寄せにする


def _frame(slide: Path, out: Path, genre: dict, index: int, total: int, top_text: str, account_name: str) -> Path:
    """4:5 のスライドを 9:16 の紙のキャンバスに貼り、上に一言、下に進み具合を入れる."""
    theme = Theme(genre)
    canvas = _paper(theme, index, size=(REEL_W, REEL_H))
    w, h = int(SLIDE_W * SCALE), int(SLIDE_H * SCALE)
    img = Image.open(slide).convert("RGB").resize((w, h), Image.LANCZOS)
    top = 300
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(((REEL_W - w) // 2 + 6, top + 12, (REEL_W + w) // 2 + 6, top + h + 12),
                                             radius=24, fill=(60, 50, 40, 50))
    from PIL import ImageFilter

    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(12))).convert("RGB")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=24, fill=255)
    canvas.paste(img, ((REEL_W - w) // 2, top), mask)
    _tape(canvas, (REEL_W - w) // 2 + 90, top + 4, 170, 50, theme.tape(1), -10)
    _tape(canvas, (REEL_W + w) // 2 - 90, top + 4, 170, 50, theme.tape(2), 8)
    draw = ImageDraw.Draw(canvas)
    if top_text:
        f = _font("hand", 60)
        tw = draw.textlength(top_text, font=f)
        _marker(canvas, (REEL_W - int(tw)) // 2, 168, (REEL_W + int(tw)) // 2, 208, theme.marker, seed="reeltop")
        draw = ImageDraw.Draw(canvas)
        draw.text((REEL_W // 2, 170), top_text, font=f, fill=theme.ink, anchor="mm")
    # 進み具合のドット（あと何枚か分かると、最後まで見てもらいやすい）
    y = top + h + 60
    gap = 44
    x0 = REEL_W // 2 - gap * (total - 1) // 2
    for i in range(total):
        r = 11 if i == index else 8
        fill = theme.accent if i <= index else theme.dot
        draw.ellipse((x0 + gap * i - r, y - r, x0 + gap * i + r, y + r), fill=fill)
    if account_name:
        draw.text((REEL_W // 2, y + 60), f"@{account_name}", font=_font("regular", 34), fill=theme.sub, anchor="mm")
    canvas.save(out, "PNG")
    return out


def build_reel(slides: list[Path], out_path: Path, *, genre: dict, account_name: str = "",
               top_text: str = "保存しておくと便利だよ", seconds: float = 3.0, cover_seconds: float = 3.5,
               fade: float = 0.4, bgm: Path | None = None) -> Path:
    """スライドを順に表示し、切り替え時に短いクロスフェードを入れた MP4 を作る."""
    if len(slides) < 2:
        raise ValueError("リールには2枚以上の画像が必要です")
    bgm = bgm if bgm is not None else (BGM_FILE if BGM_FILE.exists() else None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        frames = [_frame(s, Path(tmp) / f"f{i:02d}.png", genre, i, len(slides), top_text, account_name)
                  for i, s in enumerate(slides)]
        durations = [cover_seconds] + [seconds] * (len(frames) - 1)
        cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
        for f in frames:
            cmd += ["-i", str(f)]
        total = sum(durations) + fade
        if bgm:
            cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_idx = len(frames)

        # 各スライドをゆっくりズーム（1.00 → 1.04）させてから、クロスフェードでつなぐ
        parts = []
        for i, d in enumerate(durations):
            n = int((d + fade) * FPS)
            parts.append(f"[{i}:v]scale={REEL_W * 2}:{REEL_H * 2},"
                         f"zoompan=z='1+0.04*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:"
                         f"s={REEL_W}x{REEL_H}:fps={FPS},setsar=1[z{i}]")
        prev, offset = "[z0]", 0.0
        for i in range(1, len(frames)):
            offset += durations[i - 1]
            label = f"[v{i}]"
            parts.append(f"{prev}[z{i}]xfade=transition=fade:duration={fade}:offset={offset:.2f}{label}")
            prev = label
        parts.append(f"{prev}format=yuv420p[vout]")
        audio = f"[{audio_idx}:a]atrim=0:{total:.2f},afade=t=out:st={max(0, total - 1):.2f}:d=1,volume=0.6[aout]"
        cmd += ["-filter_complex", ";".join(parts + [audio]), "-map", "[vout]", "-map", "[aout]",
                "-t", f"{total:.2f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-movflags", "+faststart", str(out_path)]
        subprocess.run(cmd, check=True)
    return out_path


def probe(path: Path) -> str:
    """動画の情報（テスト・確認用）."""
    r = subprocess.run([_ffmpeg(), "-i", str(path)], capture_output=True, text=True)
    return r.stderr
