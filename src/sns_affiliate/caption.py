"""Claude で Instagram 投稿文と画像カード用テキストを生成する."""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from .rakuten import Item

SYSTEM_PROMPT = """\
あなたは日本のInstagramで商品紹介アカウントを運用するプロの編集者です。
楽天市場の商品情報をもとに、保存・シェアされやすい投稿を作ります。

守ること:
- 景品表示法・ステマ規制に従う。広告であることを隠さない（PR表記はシステム側で付けるので本文に重複させなくてよい）。
- 商品情報にない性能・効果・実体験を作らない。「使ってみたら」など体験談の捏造は禁止。レビュー件数や評価は与えられた数値だけを使う。
- 医薬品的な効能（痩せる・治る等）や、投資で儲かる等の断定・保証表現は禁止。
- Instagramのキャプションではリンクがクリックできないため、購入導線は「プロフィールのリンク」に誘導する。
- 読みやすい改行と絵文字を適度に使う。誇張しすぎない、信頼される語り口。
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "画像カード上部に大きく載せるフック。全角18文字以内。",
        },
        "points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "画像カードに載せる推しポイント3つ。各全角20文字以内。",
        },
        "short_name": {
            "type": "string",
            "description": "商品の短い呼び名（ブランド+商品種別など）。全角24文字以内。",
        },
        "caption": {
            "type": "string",
            "description": "キャプション本文（ハッシュタグは含めない）。300〜600文字。",
        },
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "#から始まるハッシュタグ 8〜12個。",
        },
    },
    "required": ["headline", "points", "short_name", "caption", "hashtags"],
    "additionalProperties": False,
}


@dataclass
class PostCopy:
    headline: str
    points: list[str]
    short_name: str
    caption: str
    hashtags: list[str]


def build_prompt(item: Item, genre: dict, number: int) -> str:
    caution = genre.get("caution")
    return f"""\
ジャンル: {genre['label']}
投稿番号: #{number:03d}（プロフィールリンク先の一覧ページで同じ番号から購入ページに飛べます）
{f'このジャンルの注意事項: {caution}' if caution else ''}

<item>
商品名: {item.name}
価格: {item.price:,}円（税込・投稿時点）
レビュー: 平均{item.review_average:.2f} / {item.review_count:,}件
ショップ: {item.shop_name}
商品説明（抜粋）: {item.caption}
</item>

この商品の紹介投稿を作ってください。
キャプションの最後は「詳しくはプロフィールのリンク → #{number:03d} からチェックしてね」の趣旨で締めてください。
ハッシュタグには次のジャンル定番タグも含めてください: {' '.join(genre.get('hashtags', []))}
"""


class CaptionGenerator:
    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic()

    def generate(self, item: Item, genre: dict, number: int) -> PostCopy:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(item, genre, number)}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # 安全分類器による誤拒否時に、サーバー側で別モデルへ自動フォールバック
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude が生成を拒否しました: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("出力が max_tokens で途切れました")
        text = next(b.text for b in response.content if b.type == "text")
        return parse_copy(json.loads(text))


def parse_copy(data: dict) -> PostCopy:
    tags = []
    for tag in data["hashtags"]:
        tag = tag.strip().replace(" ", "")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        if tag not in tags:
            tags.append(tag)
    return PostCopy(
        headline=data["headline"].strip(),
        points=[p.strip() for p in data["points"]][:3],
        short_name=data["short_name"].strip(),
        caption=data["caption"].strip(),
        hashtags=tags[:15],
    )


def compose_caption(copy: PostCopy, item: Item) -> str:
    """PR表記・価格注記・ハッシュタグを付けて最終キャプションを作る（Instagram上限2,200文字）."""
    tags = copy.hashtags
    if "#PR" not in tags:
        tags = ["#PR", *tags]
    body = (
        "【PR】楽天アフィリエイトを利用しています\n\n"
        f"{copy.caption}\n\n"
        f"💰 {item.price:,}円（税込・投稿時点の価格です。最新の価格は販売ページでご確認ください）\n\n"
        + " ".join(tags[:30])
    )
    return body[:2200]
