"""Claude で Instagram 投稿文と画像カード用テキストを生成する."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from .rakuten import Item

SYSTEM_PROMPT = """\
あなたは日本のInstagramで商品紹介アカウントを運用するプロの編集者です。
楽天市場の商品情報をもとに、保存・シェアされやすい投稿を作ります。

守ること:
- 景品表示法・ステマ規制に従う。広告であることを隠さない（PR表記はシステム側で付けるので本文に重複させなくてよい）。
- 商品情報にない性能・効果・実体験を作らない。レビュー件数や評価は与えられた数値だけを使う。
- 体験談は <owner_comment>（投稿者本人の感想）に書かれている内容だけを使い、一人称で自然に膨らませてよい。書かれていない体験を足さない。<owner_comment> が無い場合は体験談を一切書かず、「使ってみた」「愛用中」のような表現もしない。
- 医薬品的な効能（痩せる・治る等）や、投資で儲かる等の断定・保証表現は禁止。
- 「最安」「No.1」など根拠が示せない最上級表現は使わない。
- Instagramのキャプションではリンクがクリックできないため、購入導線は「プロフィールのリンク」に誘導する。
- 読みやすい改行と絵文字を適度に使う。誇張しすぎない、信頼される語り口。
"""

HASHTAGS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": "#から始まるハッシュタグ 8〜12個。",
}

SINGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "画像カード上部に大きく載せるフック。全角18文字以内。"},
        "points": {"type": "array", "items": {"type": "string"},
                   "description": "画像カードに載せる推しポイント3つ。各全角20文字以内。"},
        "short_name": {"type": "string", "description": "商品の短い呼び名（ブランド+商品種別など）。全角24文字以内。"},
        "caption": {"type": "string", "description": "キャプション本文（ハッシュタグは含めない）。300〜600文字。"},
        "hashtags": HASHTAGS_SCHEMA,
        "slide_captions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2枚目以降の写真に載せる一言テロップ（各全角16文字以内）。指定された枚数ちょうど。0枚なら空配列。",
        },
    },
    "required": ["headline", "points", "short_name", "caption", "hashtags", "slide_captions"],
    "additionalProperties": False,
}

ROUNDUP_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string",
                  "description": "表紙の大見出し。全角20文字以内。例: 「レビュー高評価の旅行ポーチ5選」"},
        "subtitle": {"type": "string", "description": "表紙の小見出し。全角24文字以内。"},
        "items": {
            "type": "array",
            "description": "与えられた商品と同じ順番・同じ数。",
            "items": {
                "type": "object",
                "properties": {
                    "short_name": {"type": "string", "description": "商品の短い呼び名。全角20文字以内。"},
                    "best_for": {"type": "string", "description": "どんな人向けか。全角16文字以内。例: 「荷物を減らしたい人に」"},
                    "points": {"type": "array", "items": {"type": "string"},
                               "description": "特徴2つ。各全角18文字以内。商品情報に書かれている事実だけ。"},
                },
                "required": ["short_name", "best_for", "points"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string",
                    "description": "キャプションの導入文（商品一覧とハッシュタグはシステムが付けるので含めない）。150〜350文字。"},
        "hashtags": HASHTAGS_SCHEMA,
    },
    "required": ["title", "subtitle", "items", "caption", "hashtags"],
    "additionalProperties": False,
}


@dataclass
class PostCopy:
    headline: str
    points: list[str]
    short_name: str
    caption: str
    hashtags: list[str]
    slide_captions: list[str] = field(default_factory=list)


@dataclass
class RoundupItemCopy:
    short_name: str
    best_for: str
    points: list[str]


@dataclass
class RoundupCopy:
    title: str
    subtitle: str
    items: list[RoundupItemCopy]
    caption: str
    hashtags: list[str]


def _item_block(item: Item, idx: int | None = None) -> str:
    head = f'<item index="{idx}">' if idx is not None else "<item>"
    return f"""{head}
商品名: {item.name}
価格: {item.price:,}円（税込・投稿時点）
レビュー: 平均{item.review_average:.2f} / {item.review_count:,}件
ショップ: {item.shop_name}
商品説明（抜粋）: {item.caption}
</item>"""


def _genre_header(genre: dict, number: int) -> str:
    caution = genre.get("caution")
    return (f"ジャンル: {genre['label']}\n"
            f"投稿番号: #{number:03d}（プロフィールリンク先の一覧ページで同じ番号から購入ページに飛べます）\n"
            + (f"このジャンルの注意事項: {caution}\n" if caution else ""))


def build_prompt(item: Item, genre: dict, number: int, comment: str = "", extra_photos: int = 0) -> str:
    owner = (f"<owner_comment>\n{comment}\n</owner_comment>" if comment
             else "（投稿者本人の感想はありません。体験談は書かないでください）")
    slides = (f"投稿は自分で撮った写真のカルーセルです。2枚目以降の写真は{extra_photos}枚あるので、slide_captions を"
              f"{extra_photos}個作ってください（添付した写真を見て、それぞれの写真の内容に合った一言に。"
              "1枚目は表紙なので headline と合うように）。" if extra_photos else "slide_captions は空配列にしてください。")
    return f"""{_genre_header(genre, number)}
{_item_block(item)}

{owner}

この商品の紹介投稿を作ってください。
{slides}
キャプションの最後は「詳しくはプロフィールのリンク → #{number:03d} からチェックしてね」の趣旨で締めてください。
ハッシュタグには次のジャンル定番タグも含めてください: {' '.join(genre.get('hashtags', []))}
"""


def build_roundup_prompt(items: list[Item], genre: dict, number: int, theme: str, comment: str = "") -> str:
    blocks = "\n".join(_item_block(it, i) for i, it in enumerate(items, 1))
    owner = (f"<owner_comment>\n{comment}\n</owner_comment>\n（感想がどの商品のものか明確な場合だけ、その商品の紹介に反映してよい）"
             if comment else "（投稿者本人の感想はありません。体験談は書かず、比較・まとめとして紹介してください）")
    return f"""{_genre_header(genre, number)}
テーマ: {theme}

{blocks}

{owner}

これらの商品を比較・まとめるカルーセル投稿（{len(items)}選）を作ってください。
- items は上の商品と同じ順番で{len(items)}個。商品ごとの違い（価格帯・向いている人）が分かるようにする。
- 読者が「保存して後で比べたい」と思える構成にする。
ハッシュタグには次のジャンル定番タグも含めてください: {' '.join(genre.get('hashtags', []))}
"""


def _photo_b64(path: Path, max_side: int = 1024) -> str:
    """写真を縮小して base64 JPEG にする（トークン節約）."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


class CaptionGenerator:
    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic()

    def _call(self, content: list[dict], schema: dict) -> dict:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            # 安全分類器による誤拒否時に、サーバー側で別モデルへ自動フォールバック
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude が生成を拒否しました: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("出力が max_tokens で途切れました")
        return json.loads(next(b.text for b in response.content if b.type == "text"))

    def generate(self, item: Item, genre: dict, number: int, comment: str = "",
                 photos: list[Path] | None = None) -> PostCopy:
        photos = photos or []
        extra_photos = max(0, len(photos) - 1)
        content: list[dict] = []
        for i, photo in enumerate(photos, 1):
            content.append({"type": "text", "text": f"写真{i}枚目:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": _photo_b64(photo)}})
        content.append({"type": "text", "text": build_prompt(item, genre, number, comment, extra_photos)})
        return parse_copy(self._call(content, SINGLE_SCHEMA), extra_photos)

    def generate_roundup(self, items: list[Item], genre: dict, number: int, theme: str,
                         comment: str = "") -> RoundupCopy:
        prompt = build_roundup_prompt(items, genre, number, theme, comment)
        return parse_roundup(self._call([{"type": "text", "text": prompt}], ROUNDUP_SCHEMA), items)


def normalize_hashtags(raw: list[str], limit: int = 15) -> list[str]:
    tags: list[str] = []
    for tag in raw:
        tag = tag.strip().replace(" ", "").replace("　", "")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        if tag not in tags:
            tags.append(tag)
    return tags[:limit]


def parse_copy(data: dict, extra_photos: int = 0) -> PostCopy:
    return PostCopy(
        headline=data["headline"].strip(),
        points=[p.strip() for p in data["points"]][:3],
        short_name=data["short_name"].strip(),
        caption=data["caption"].strip(),
        hashtags=normalize_hashtags(data["hashtags"]),
        slide_captions=([c.strip() for c in data.get("slide_captions", [])] + [""] * extra_photos)[:extra_photos],
    )


def parse_roundup(data: dict, items: list[Item]) -> RoundupCopy:
    copies = []
    raw_items = data.get("items", [])
    for i, item in enumerate(items):
        raw = raw_items[i] if i < len(raw_items) else {}
        copies.append(RoundupItemCopy(
            short_name=(raw.get("short_name") or item.name[:20]).strip(),
            best_for=(raw.get("best_for") or "").strip(),
            points=[p.strip() for p in raw.get("points", [])][:2],
        ))
    return RoundupCopy(title=data["title"].strip(), subtitle=data["subtitle"].strip(), items=copies,
                       caption=data["caption"].strip(), hashtags=normalize_hashtags(data["hashtags"]))


PR_LINE = "【PR】楽天アフィリエイトを利用しています"
PRICE_NOTE = "※価格は投稿時点のものです。最新の価格は販売ページでご確認ください"


def _with_pr(tags: list[str]) -> str:
    return " ".join((tags if "#PR" in tags else ["#PR", *tags])[:30])


def compose_caption(copy: PostCopy, item: Item) -> str:
    """PR表記・価格注記・ハッシュタグを付けて最終キャプションを作る（Instagram上限2,200文字）."""
    body = (
        f"{PR_LINE}\n\n"
        f"{copy.caption}\n\n"
        f"💰 {item.price:,}円（税込・投稿時点の価格です。最新の価格は販売ページでご確認ください）\n\n"
        + _with_pr(copy.hashtags)
    )
    return body[:2200]


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def compose_roundup_caption(copy: RoundupCopy, items: list[Item], number: int) -> str:
    """価格・レビューは Claude に書かせずコード側で正確に差し込む."""
    lines = []
    for i, (c, it) in enumerate(zip(copy.items, items)):
        lines.append(f"{CIRCLED[i]} {c.short_name}｜¥{it.price:,}｜★{it.review_average:.1f}（{it.review_count:,}件）")
        if c.best_for:
            lines.append(f"　→ {c.best_for}")
    tail = ("\n\n📝 今回紹介したアイテム\n" + "\n".join(lines)
            + f"\n\n🛒 購入はプロフィールのリンク → #{number:03d} から\n{PRICE_NOTE}\n\n" + _with_pr(copy.hashtags))
    head = f"{PR_LINE}\n\n"
    room = 2200 - len(head) - len(tail)
    intro = copy.caption if len(copy.caption) <= room else copy.caption[: max(0, room - 1)] + "…"
    return head + intro + tail
