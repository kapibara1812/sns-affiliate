"""カルーセルの画像をつないで、Instagram リール用の縦長動画（1080x1920, 9:16）を作る."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .image_card import HEIGHT as SLIDE_H, WIDTH as SLIDE_W, _font, _hex

REEL_W, REEL_H = 1080, 1920
FPS = 30
BGM_FILE = config.ROOT / "assets" / "bgm.mp3"  # 任意。置けばBGMとして使う（著作権フリーの音源のみ）


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def _frame(slide: Path, out: Path, genre: dict, index: int, total: int, top_text: str, account_name: str) -> Path:
    """4:5 のスライドを 9:16 のキャンバスの中央に置き、上下に案内を入れる."""
    bg = _hex(genre.get("color", "#0B1F3A"))
    accent = _hex(genre.get("accent", "#FF5A36"))
    canvas = Image.new("RGB", (REEL_W, REEL_H), bg)
    img = Image.open(slide).convert("RGB").resize((SLIDE_W, SLIDE_H))
    top = (REEL_H - SLIDE_H) // 2
    canvas.paste(img, (0, top))
    draw = ImageDraw.Draw(canvas)
    if top_text:
        draw.text((REEL_W // 2, top // 2), top_text, font=_font("bold", 52), fill=(255, 255, 255), anchor="mm")
    # 進み具合のバー（リールは途中で離脱されやすいので「あと何枚か」を見せる）
    bar_y = top + SLIDE_H + 70
    seg = (REEL_W - 160) // total
    for i in range(total):
        x0 = 80 + i * seg
        draw.rounded_rectangle((x0 + 6, bar_y, x0 + seg - 6, bar_y + 12), radius=6,
                               fill=accent if i <= index else (70, 85, 110))
    if account_name:
        draw.text((REEL_W // 2, bar_y + 90), f"@{account_name}", font=_font("regular", 36),
                  fill=(203, 213, 225), anchor="mm")
    canvas.save(out, "PNG")
    return out


def build_reel(slides: list[Path], out_path: Path, *, genre: dict, account_name: str = "",
               top_text: str = "保存して見返してね", seconds: float = 3.0, cover_seconds: float = 3.5,
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
        for f, d in zip(frames, durations):
            cmd += ["-loop", "1", "-t", f"{d + fade:.2f}", "-framerate", str(FPS), "-i", str(f)]
        total = sum(durations) + fade
        if bgm:
            cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_idx = len(frames)

        # xfade をつなぐ
        parts, prev, offset = [], "[0:v]", 0.0
        for i in range(1, len(frames)):
            offset += durations[i - 1]
            label = f"[v{i}]"
            parts.append(f"{prev}[{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.2f}{label}")
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
