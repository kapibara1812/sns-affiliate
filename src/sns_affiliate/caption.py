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


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _strs(desc: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


EXPLAINER_SCHEMA = {
    "type": "object",
    "properties": {
        "cover_hook": _str("1枚目の大見出し。思わず指が止まる問いかけや『あるある』。全角22文字以内。"),
        "cover_sub": _str("1枚目の小見出し。この投稿で分かること。全角26文字以内。"),
        "short_name": _str("サービス名を短くしたもの。全角16文字以内。"),
        "pains_title": _str("2枚目の見出し。例『こんな悩み、ありませんか？』。全角16文字以内。"),
        "pains": _strs("読者が感じている困りごと3つ。一般的な内容。各全角22文字以内。"),
        "compare": {
            "type": "object",
            "description": "一般的な選択肢の比較表。比べる意味がない場合は title を空文字、rows を空配列にする。",
            "properties": {
                "title": _str("比較表の見出し。全角16文字以内。"),
                "columns": _strs("比べる選択肢（2〜3個）。他社名ではなく一般的な手段の種類で書く。紹介するサービスの種類を最後に置く。各全角8文字以内。"),
                "rows": {
                    "type": "array",
                    "description": "比較の観点（3〜4行）。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": _str("観点。例『準備の手間』。全角8文字以内。"),
                            "values": _strs("columns と同じ順番・同じ数の評価。◎○△× か、ごく短い言葉（全角6文字以内）。具体的な金額は書かない。"),
                        },
                        "required": ["label", "values"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "columns", "rows"],
            "additionalProperties": False,
        },
        "steps_title": _str("使い方・選び方の手順の見出し。不要なら空文字。全角16文字以内。"),
        "steps": {
            "type": "array",
            "description": "一般的な手順（0〜4個）。不要なら空配列。",
            "items": {
                "type": "object",
                "properties": {
                    "title": _str("手順の名前。全角12文字以内。"),
                    "desc": _str("ひとこと説明。全角26文字以内。"),
                },
                "required": ["title", "desc"],
                "additionalProperties": False,
            },
        },
        "features": _strs("サービスのポイント3〜4個。<service> の『公式に確認できる特徴』に書かれた事実だけ。各全角24文字以内。"),
        "fit": _strs("こんな人に向いている、を3つ。各全角22文字以内。"),
        "caption": _str("キャプション本文（ハッシュタグは含めない）。300〜600文字。保存を促す一文を入れる。"),
        "hashtags": HASHTAGS_SCHEMA,
    },
    "required": ["cover_hook", "cover_sub", "short_name", "pains_title", "pains", "compare", "steps_title",
                 "steps", "features", "fit", "caption", "hashtags"],
    "additionalProperties": False,
}


PATTERNS_SCHEMA = {
    "type": "object",
    "properties": {
        "patterns": {
            "type": "array",
            "description": "伸びた投稿の1行目（表紙の見出し・キャプション1行目）に共通する型を5つ。",
            "items": {
                "type": "object",
                "properties": {
                    "name": _str("型の名前。例『数字で損を突く』。全角16文字以内。"),
                    "template": _str("穴埋めできるテンプレート。例『{商品ジャンル}選びで{数字}割の人が損してる』。"),
                    "why": _str("なぜ刺さるのか（データから読み取れる理由）。全角60文字以内。"),
                    "examples": _strs("集めた投稿の中から、この型に当てはまる1行目を2〜3個（そのまま引用）。"),
                },
                "required": ["name", "template", "why", "examples"],
                "additionalProperties": False,
            },
        },
        "structures": _strs("伸びたカルーセル・リールに多い構成（例『悩み→比較→おすすめ3選→まとめ』）を3つまで。"),
        "design_notes": _strs("デザインの共通点（配色・文字の大きさ・写真の使い方など）を5つまで。"),
        "summary": _str("全体のまとめ。全角200文字以内。"),
    },
    "required": ["patterns", "structures", "design_notes", "summary"],
    "additionalProperties": False,
}


@dataclass
class ExplainerCopy:
    cover_hook: str
    cover_sub: str
    short_name: str
    pains_title: str
    pains: list[str]
    compare_title: str
    compare_columns: list[str]
    compare_rows: list[tuple[str, list[str]]]
    steps_title: str
    steps: list[tuple[str, str]]
    features: list[str]
    fit: list[str]
    caption: str
    hashtags: list[str]

    def all_text(self) -> list[str]:
        out = [self.cover_hook, self.cover_sub, self.short_name, self.pains_title, *self.pains,
               self.compare_title, *self.compare_columns, self.steps_title, *self.features, *self.fit, self.caption]
        for label, values in self.compare_rows:
            out += [label, *values]
        for t, d in self.steps:
            out += [t, d]
        return out


def parse_explainer(data: dict) -> ExplainerCopy:
    cmp = data.get("compare") or {}
    cols = [c.strip() for c in cmp.get("columns", [])][:3]
    rows = []
    for r in cmp.get("rows", [])[:4]:
        vals = [v.strip() for v in r.get("values", [])]
        if cols and len(vals) >= len(cols):
            rows.append((r.get("label", "").strip(), vals[: len(cols)]))
    if len(cols) < 2 or not rows:
        cols, rows = [], []
    steps = [(s.get("title", "").strip(), s.get("desc", "").strip()) for s in data.get("steps", [])][:4]
    return ExplainerCopy(
        cover_hook=data["cover_hook"].strip(),
        cover_sub=data["cover_sub"].strip(),
        short_name=data["short_name"].strip(),
        pains_title=data["pains_title"].strip() or "こんな悩み、ありませんか？",
        pains=[x.strip() for x in data["pains"] if x.strip()][:3],
        compare_title=(cmp.get("title") or "").strip() if rows else "",
        compare_columns=cols,
        compare_rows=rows,
        steps_title=data.get("steps_title", "").strip() if steps else "",
        steps=steps,
        features=[x.strip() for x in data["features"] if x.strip()][:4],
        fit=[x.strip() for x in data["fit"] if x.strip()][:3],
        caption=data["caption"].strip(),
        hashtags=normalize_hashtags(data["hashtags"]),
    )


def build_a8_explainer_prompt(program, genre: dict, number: int, avoid: list[str] | None = None) -> str:
    facts = "\n".join(f"- {f}" for f in program.facts)
    ng = program.ng_words + (avoid or [])
    return f"""{_genre_header(genre, number)}
<service>
サービス名: {program.name}
料金の表記: {program.price_label or '（指定なし。料金には触れない）'}
公式に確認できる特徴:
{facts}
{f'補足: {program.notes}' if program.notes else ''}
</service>

（投稿者本人の利用経験はありません。体験談は書かないでください）

このサービス（A8.net の広告案件）を紹介する、Instagram の「図解カルーセル投稿」の原稿を作ってください。
読者が『保存して後で見返したい』と思う、役に立つ情報 → 最後にサービス紹介、の流れにします。

スライド構成（システムがこの順に画像にします）:
1. 表紙（cover_hook / cover_sub）: 読者の『あるある』や疑問で指を止める
2. 悩み（pains）: 読者がこのジャンルで困りがちなこと
3. 比較表（compare）: 一般的な選択肢を、手間・使いやすさなどの観点で ◎○△× で比べる
4. 手順（steps）: 一般的な使い方や選び方（不要なら空）
5. 公式画像（システムが入れる）
6. サービスのポイント（features）
7. こんな人に（fit）
8. まとめ・料金・購入導線（システムが入れる）

守ること:
- features は <service> の特徴に書かれた事実だけを使う。料金・特典・数値を新しく作らない。
- pains / compare / steps は一般的な内容にとどめ、具体的な金額・他社名・他社の性能は書かない。
- 比較表で、紹介するサービスの種類を不当に持ち上げない（短所があれば△を付ける）。
- キャプションには「条件や最新の内容は公式サイトで確認してね」という一文を入れる。
{f'- 次の表現は広告主のルールで禁止なので使わない: {"、".join(ng)}' if ng else ''}
キャプションの最後は「詳しくはプロフィールのリンク → #{number:03d} からチェックしてね」の趣旨で締めてください。
ハッシュタグには次のジャンル定番タグも含めてください: {' '.join(genre.get('hashtags', []))}
"""


def build_draft_prompt(subject: dict, genre: dict, pattern: dict, audience: str, avoid: list[str] | None = None,
                       remix_of: dict | None = None) -> str:
    """下書き用の図解カルーセル原稿のプロンプト.

    subject: {"kind": "a8"|"rakuten"|"tips", "name", "facts", "price_label", "notes", "ng_words"}
    """
    ng = list(genre.get("ng_words", [])) + list(subject.get("ng_words", [])) + list(avoid or [])
    caution = genre.get("caution", "")
    if subject["kind"] == "tips":
        about = f"""<topic>
テーマ: {subject['name']}
（商品の紹介はしない、お役立ち投稿。features には役立つポイントを書く。体験談は書かない）
</topic>"""
        tail = "キャプションの最後は「他の投稿もプロフィールからどうぞ。保存して見返してね」の趣旨で締めてください。"
    else:
        facts = "\n".join(f"- {f}" for f in subject.get("facts", []))
        about = f"""<service>
紹介するもの: {subject['name']}
料金の表記: {subject.get('price_label') or '（指定なし。料金には触れない）'}
公式に確認できる特徴（features はここに書かれた事実だけを使う）:
{facts}
{f"補足: {subject['notes']}" if subject.get('notes') else ''}
</service>
（投稿者本人の利用経験はありません。体験談は書かないでください）"""
        tail = "キャプションの最後は「詳しくはプロフィールのリンク → 番号からチェックしてね」の趣旨で締めてください（番号はシステムが入れるので書かない）。"
    remix = ""
    if remix_of:
        remix = f"""
<previous_hit>
この投稿は以前よく保存された投稿の作り直しです。同じ内容の繰り返しにならないよう、切り口（対象の人・比べる観点・語り口）を変えてください。
前回の表紙: {remix_of.get('headline', '')}
前回のキャプション冒頭: {remix_of.get('caption', '')[:200]}
</previous_hit>"""
    return f"""ジャンル: {genre['label']}
語りかける相手: {audience}
{f'このジャンルの注意事項: {caution}' if caution else ''}

{about}
{remix}
<hook_pattern>
表紙（cover_hook）とキャプションの1行目は、次の型を使ってください。
型の名前: {pattern['name']}
テンプレート: {pattern['template']}
刺さる理由: {pattern.get('why', '')}
</hook_pattern>

Instagram の図解カルーセル投稿の原稿を作ってください（表紙 → 悩み → 比較 → 手順 → ポイント → こんな人に → まとめ）。
- pains / compare / steps は一般的な内容にとどめ、具体的な金額・他社名・他社の性能は書かない。
- 比較表で、紹介するものを不当に持ち上げない。
{f'- 次の表現は使わない: {"、".join(dict.fromkeys(ng))}' if ng else ''}
{tail}
ハッシュタグには次のジャンル定番タグも含めてください: {' '.join(genre.get('hashtags', []))}
"""


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


def build_a8_prompt(program, genre: dict, number: int, comment: str = "", extra_photos: int = 0,
                    avoid: list[str] | None = None) -> str:
    facts = "\n".join(f"- {f}" for f in program.facts)
    owner = (f"<owner_comment>\n{comment}\n</owner_comment>" if comment
             else "（投稿者本人の利用経験はありません。体験談は書かないでください）")
    ng = program.ng_words + (avoid or [])
    slides = (f"投稿は自分で撮った写真のカルーセルです。2枚目以降の写真は{extra_photos}枚あるので、slide_captions を"
              f"{extra_photos}個作ってください（添付した写真を見て、それぞれの内容に合った一言に）。"
              if extra_photos else "slide_captions は空配列にしてください。")
    return f"""{_genre_header(genre, number)}
<service>
サービス名: {program.name}
料金の表記: {program.price_label or '（指定なし。料金には触れず「詳しくは公式サイトで」とする）'}
公式に確認できる特徴:
{facts}
{f'補足: {program.notes}' if program.notes else ''}
</service>

{owner}

このサービス（A8.net の広告案件）の紹介投稿を作ってください。
- <service> に書かれた特徴だけを使う。料金・特典・期間・数字を新しく作らない。
- 「条件や最新の内容は公式サイトで確認してね」という一文を本文に入れる。
- short_name はサービス名を短くしたもの。
{f'- 次の表現は広告主のルールで禁止なので使わない: {"、".join(ng)}' if ng else ''}
{slides}
キャプションの最後は「詳しくはプロフィールのリンク → #{number:03d} からチェックしてね」の趣旨で締めてください。
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

    def generate_a8(self, program, genre: dict, number: int, comment: str = "",
                    photos: list[Path] | None = None) -> PostCopy:
        """A8 案件の投稿文。広告主の NG ワードが入っていたら1回だけ作り直す."""
        from .a8 import find_ng_words

        photos = photos or []
        extra_photos = max(0, len(photos) - 1)
        images: list[dict] = []
        for i, photo in enumerate(photos, 1):
            images.append({"type": "text", "text": f"写真{i}枚目:"})
            images.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": _photo_b64(photo)}})
        avoid: list[str] = []
        for _ in range(2):
            prompt = build_a8_prompt(program, genre, number, comment, extra_photos, avoid)
            copy = parse_copy(self._call(images + [{"type": "text", "text": prompt}], SINGLE_SCHEMA), extra_photos)
            found = find_ng_words(program, copy.headline, copy.short_name, copy.caption,
                                  *copy.points, *copy.slide_captions)
            if not found:
                return copy
            avoid = found
        raise RuntimeError(f"A8案件 {program.id}: 禁止表現 {found} を含まない投稿文を作れませんでした")

    def generate_a8_explainer(self, program, genre: dict, number: int) -> ExplainerCopy:
        """写真がない A8 案件用の図解カルーセル原稿。NG ワードがあれば1回だけ作り直す."""
        from .a8 import find_ng_words

        avoid: list[str] = []
        for _ in range(2):
            prompt = build_a8_explainer_prompt(program, genre, number, avoid)
            copy = parse_explainer(self._call([{"type": "text", "text": prompt}], EXPLAINER_SCHEMA))
            found = find_ng_words(program, *copy.all_text())
            if not found:
                return copy
            avoid = found
        raise RuntimeError(f"A8案件 {program.id}: 禁止表現 {found} を含まない投稿文を作れませんでした")

    def generate_draft(self, subject: dict, genre: dict, pattern: dict, audience: str,
                       remix_of: dict | None = None) -> "ExplainerCopy":
        """下書き1本分の原稿。ジャンル・案件の禁止表現が入っていたら1回だけ作り直す."""
        ng = list(genre.get("ng_words", [])) + list(subject.get("ng_words", []))
        avoid: list[str] = []
        found: list[str] = []
        for _ in range(2):
            prompt = build_draft_prompt(subject, genre, pattern, audience, avoid, remix_of)
            copy = parse_explainer(self._call([{"type": "text", "text": prompt}], EXPLAINER_SCHEMA))
            joined = "\n".join(copy.all_text())
            found = [w for w in ng if w and w in joined]
            if not found:
                return copy
            avoid = found
        raise RuntimeError(f"禁止表現 {found} を含まない下書きを作れませんでした（{subject['name']}）")

    def analyze_patterns(self, rows_text: str, genre_label: str) -> dict:
        """調査で集めた投稿（1行目と反応の数）から、刺さる1行目の型を5つ抜き出す."""
        prompt = f"""以下は、Instagram の「{genre_label}」ジャンルで伸びている日本語アカウントの投稿を集めたものです。
反応（いいね・保存・再生）が多い投稿ほど重視して、
「この投稿の1行目（表紙の見出し・キャプションの1行目）に共通していること」を5つの型にまとめてください。
型は、別の商品にも使い回せるテンプレートにしてください。データから言えないことは書かないでください。

<posts>
{rows_text}
</posts>"""
        return self._call([{"type": "text", "text": prompt}], PATTERNS_SCHEMA)

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


def compose_a8_caption(copy: PostCopy, program) -> str:
    price = f"💰 {program.price_label}（投稿時点）\n" if program.price_label else ""
    body = (
        "【PR】A8.netのアフィリエイト広告を利用しています\n\n"
        f"{copy.caption}\n\n"
        f"{price}※料金・特典・条件は変わることがあります。最新の内容は公式サイトでご確認ください\n\n"
        + _with_pr(copy.hashtags)
    )
    return body[:2200]


def compose_a8_explainer_caption(copy: ExplainerCopy, program) -> str:
    return compose_a8_caption(PostCopy(headline=copy.cover_hook, points=copy.features, short_name=copy.short_name,
                                       caption=copy.caption, hashtags=copy.hashtags), program)
