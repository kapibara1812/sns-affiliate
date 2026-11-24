"""OpenAI の画像生成 API で、表紙のイラストと使用シーンのイメージ画像を作る.

- 実在の商品・ブランドロゴ・文字・リアルな人の顔は描かせない（商品の誤認防止と、Instagram の AI 人物ラベル対策）
- 失敗しても投稿は止めない（画像なしで、これまでどおり文字と図だけのスライドになる）
"""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import requests

from .config import Settings

API_URL = "https://api.openai.com/v1/images/generations"

# 全画像に付ける共通の指示（絵柄をそろえ、問題になりやすいものを避ける）
STYLE = (
    "Soft, friendly flat illustration with gentle hand-drawn texture, pastel colors, clean simple background, "
    "plenty of empty space, cute and approachable Japanese social media style. "
    "No text, no letters, no numbers, no logos, no brand names, no real products or packaging. "
    "No realistic humans; if a person is needed, draw a simple faceless stylized figure."
)

SIZES = {"cover": "1024x1024", "scene": "1536x1024"}


def prompt_hash(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


class ImageGenerator:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.s.openai_api_key) and self.s.generate_images

    def generate(self, prompt: str, out_path: Path, kind: str = "cover") -> Path | None:
        """プロンプトから画像を1枚作って保存する。キー未設定・失敗時は None."""
        if not self.enabled or not prompt.strip():
            return None
        body = {
            "model": self.s.image_model,
            "prompt": f"{prompt.strip()}\n\nStyle: {STYLE}",
            "size": SIZES.get(kind, "1024x1024"),
            "quality": self.s.image_quality,
            "n": 1,
        }
        headers = {"Authorization": f"Bearer {self.s.openai_api_key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                resp = self.session.post(API_URL, json=body, headers=headers, timeout=180)
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(10 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                print(f"画像生成に失敗しました（{resp.status_code}）: {resp.text[:300]}")
                return None
            item = (resp.json().get("data") or [{}])[0]
            if item.get("b64_json"):
                data = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                data = self.session.get(item["url"], timeout=60).content
            else:
                return None
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            return out_path
        return None
