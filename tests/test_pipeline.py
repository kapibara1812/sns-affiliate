import json
import random
from pathlib import Path

import pytest
from PIL import Image

from sns_affiliate import config, pipeline, storage
from sns_affiliate.caption import (PostCopy, RoundupCopy, RoundupItemCopy, compose_caption,
                                   compose_roundup_caption, parse_copy, parse_roundup)
from sns_affiliate.rakuten import Item


def make_item(code="shop:1", reviews=100, avg=4.5, price=3980, shop=None):
    return Item(item_code=code, name=f"商品{code}", price=price, item_url="https://item.rakuten.co.jp/x",
                affiliate_url=f"https://hb.afl.rakuten.co.jp/{code}", image_url="", review_average=avg,
                review_count=reviews, shop_name=shop or f"shop-{code}", caption="説明")


class FakeRakuten:
    def __init__(self, items):
        self.items = items
        self.keywords = []

    def search(self, keyword, **_):
        self.keywords.append(keyword)
        return self.items

    def lookup(self, code):
        return make_item(code)


class FakeWriter:
    def __init__(self):
        self.calls = []

    def generate(self, item, genre, number, comment="", photos=None):
        photos = photos or []
        self.calls.append((comment, photos))
        return PostCopy(headline="見出しテスト", points=["a", "b", "c"], short_name="短い名前",
                        caption=f"本文 #{number:03d}", hashtags=["#テスト"],
                        slide_captions=[f"テロップ{i}" for i in range(len(photos) - 1)])

    def generate_a8_explainer(self, program, genre, number):
        from sns_affiliate.caption import ExplainerCopy

        self.calls.append(("a8x", program.id))
        return ExplainerCopy(cover_hook="海外のネット、まだポケットWi-Fi借りてる？", cover_sub="出発前に知りたい通信の選び方",
                             short_name=program.name[:10], pains_title="こんな悩み、ありませんか？",
                             pains=["受け取りと返却が面倒", "充電が切れる", "荷物が増える"],
                             compare_title="海外の通信手段を比較", compare_columns=["ポケットWi-Fi", "ローミング", "eSIM"],
                             compare_rows=[("準備の手間", ["△", "◎", "○"]), ("荷物", ["△", "◎", "◎"]),
                                           ("料金の目安", ["○", "△", "○"])],
                             steps_title="使い方3ステップ", steps=[("出発前に購入", "公式サイトでプランを選ぶ"),
                                                              ("設定する", "QRコードを読み込む"), ("到着したらオン", "回線を切り替える")],
                             features=list(program.facts[:3]), fit=["旅行が多い人", "荷物を減らしたい人", "手続きを楽にしたい人"],
                             caption="本文", hashtags=["#旅行"])

    def generate_a8(self, program, genre, number, comment="", photos=None):
        self.calls.append(("a8", program.id, comment))
        return self.generate(None, genre, number, comment, photos)

    def generate_draft(self, subject, genre, pattern, audience, remix_of=None):
        from sns_affiliate.caption import ExplainerCopy

        self.calls.append(("draft", subject["kind"], subject["name"], pattern["name"], bool(remix_of)))
        feats = list(subject.get("facts") or ["ポイントA", "ポイントB"])[:3]
        return ExplainerCopy(cover_hook=f"{pattern['name']}：{subject['name'][:10]}", cover_sub="サブ",
                             short_name=subject["name"][:10], pains_title="こんな悩み、ありませんか？",
                             pains=["悩み1", "悩み2"], compare_title="比べると", compare_columns=["A", "B"],
                             compare_rows=[("手間", ["△", "◎"])], steps_title="", steps=[], features=feats,
                             fit=["初心者"], caption="本文です", hashtags=["#筋トレ"])

    def analyze_patterns(self, rows_text, genre_label):
        self.calls.append(("analyze", rows_text.count("\n") + 1))
        return {"patterns": [{"name": f"型{i}", "template": f"テンプレ{i}", "why": "理由", "examples": ["例"]}
                             for i in range(5)], "structures": ["悩み→比較"], "design_notes": ["白背景"], "summary": "まとめ"}

    def generate_roundup(self, items, genre, number, theme, comment=""):
        self.calls.append(("roundup", theme, comment, len(items)))
        return RoundupCopy(title=f"{theme}{len(items)}選", subtitle="サブ", caption="導入文", hashtags=["#まとめ"],
                           items=[RoundupItemCopy(f"短名{i}", f"向いている人{i}", ["特徴A", "特徴B"])
                                  for i in range(len(items))])


class FakeIG:
    def __init__(self):
        self.calls = []

    def publish(self, urls, caption):
        self.calls.append((urls, caption))
        return {"id": "m1", "permalink": "https://instagram.com/p/x"}


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POSTS_FILE", tmp_path / "data" / "posts.json")
    monkeypatch.setattr(config, "INSIGHTS_CSV", tmp_path / "data" / "insights.csv")
    monkeypatch.setattr(config, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(config, "IMAGES_DIR", tmp_path / "docs" / "images")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "docs" / "videos")
    monkeypatch.setattr(config, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(config, "DRAFT_PREVIEW_DIR", tmp_path / "docs" / "drafts")
    # 本番のジャンル設定に左右されないよう、テスト用のジャンル設定を使う
    genres = json.loads(config.GENRES_FILE.read_text(encoding="utf-8"))
    for g in genres["genres"]:
        g["enabled"] = g["id"] in {"gadget", "appliance", "travel", "training", "supplement"}
    gfile = tmp_path / "config" / "genres.json"
    gfile.parent.mkdir(parents=True, exist_ok=True)
    gfile.write_text(json.dumps(genres, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "GENRES_FILE", gfile)
    from sns_affiliate import research
    monkeypatch.setattr(research, "RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(research, "POSTS_CSV", tmp_path / "research" / "posts.csv")
    monkeypatch.setattr(research, "PATTERNS_JSON", tmp_path / "research" / "patterns.json")
    monkeypatch.setattr(research, "PATTERNS_MD", tmp_path / "research" / "patterns.md")
    monkeypatch.setenv("MAKE_REELS", "false")  # 動画生成は専用テストだけで行う（時間がかかるため）
    monkeypatch.setattr(config, "ROOT", tmp_path)
    from sns_affiliate import a8, inbox
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(a8, "PROGRAMS_FILE", tmp_path / "config" / "a8_programs.json")
    monkeypatch.setattr(pipeline, "is_a8_day", lambda settings, today=None: False)
    # storage / site はデフォルト引数でパスを束縛しているので関数側も差し替える
    monkeypatch.setattr(storage, "load_posts", lambda path=None: _load(tmp_path))
    monkeypatch.setattr(storage, "save_posts", lambda posts, path=None: _save(tmp_path, posts))
    import sns_affiliate.site as site
    orig = site.build_site
    monkeypatch.setattr(pipeline, "build_site", lambda posts, genres, title: orig(posts, genres, title, tmp_path / "docs"))
    monkeypatch.setattr(pipeline, "write_insights_csv", lambda posts: None)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    return tmp_path


@pytest.fixture
def rakuten_on(monkeypatch):
    """楽天の商品を指定したネタ（inbox）を使うテスト用。本番の初期設定はオフ."""
    monkeypatch.setenv("RAKUTEN_ENABLED", "true")
    for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "RAKUTEN_AFFILIATE_ID"):
        monkeypatch.setenv(k, "test")


def _load(root):
    p = root / "data" / "posts.json"
    return json.loads(p.read_text()) if p.exists() else []


def _save(root, posts):
    p = root / "data" / "posts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(posts, ensure_ascii=False))


def test_choose_item_filters_reviews_and_recent():
    s = config.Settings()
    items = [make_item("a", reviews=5), make_item("b", avg=3.0), make_item("c"), make_item("d")]
    got = pipeline.choose_item(items, {"d"}, s, random.Random(0))
    assert got.item_code == "c"
    assert pipeline.choose_item([make_item("x", reviews=1)], set(), s, random.Random(0)) is None


def test_genre_rotation():
    genres = config.load_genres()
    assert pipeline.pick_genre(genres, 1)["id"] == genres[0]["id"]
    assert pipeline.pick_genre(genres, len(genres) + 2)["id"] == genres[1]["id"]
    assert pipeline.pick_genre(genres, 1, "book")["id"] == "book"


def test_parse_copy_normalizes_hashtags():
    c = parse_copy({"headline": " h ", "points": ["1", "2", "3", "4"], "short_name": "n",
                    "caption": "c", "hashtags": ["ガジェット", "#ガジェット", " #便 利 ", ""]})
    assert c.hashtags == ["#ガジェット", "#便利"]
    assert c.points == ["1", "2", "3"]


def test_compose_caption_has_pr_disclosure_and_limit():
    c = PostCopy("h", ["a"], "n", "本文" * 2000, ["#x"])
    text = compose_caption(c, make_item())
    assert text.startswith("【PR】")
    assert len(text) <= 2200


def test_prepare_then_publish(tmp_project):
    s = config.Settings()
    s.public_base_url = "https://example.github.io/sns-affiliate"
    rakuten = FakeRakuten([make_item("a"), make_item("b")])
    post = pipeline.prepare(s, mode="single", rakuten=rakuten, writer=FakeWriter(), rng=random.Random(1))

    assert post["number"] == 1 and post["status"] == "prepared"
    img = Image.open(tmp_project / post["image_path"])
    assert img.size == (1080, 1350)
    html = (tmp_project / "docs" / "index.html").read_text()
    assert post["affiliate_url"] in html and 'rel="sponsored noopener"' in html

    # 同じ商品は連続で選ばれない
    post2 = pipeline.prepare(s, mode="single", rakuten=rakuten, writer=FakeWriter(), rng=random.Random(1))
    assert post2["item_code"] != post["item_code"]

    ig = FakeIG()
    done = pipeline.publish(s, ig=ig, wait=False, limit=5)
    assert [p["status"] for p in done] == ["published", "published"]
    assert ig.calls[0][0] == ["https://example.github.io/sns-affiliate/images/0001.jpg"]
    assert ig.calls[0][1].startswith("【PR】")


def test_card_with_product_image(tmp_path, monkeypatch):
    from sns_affiliate import image_card
    monkeypatch.setattr(image_card, "_fetch_image", lambda url: Image.new("RGB", (600, 400), "red"))
    out = image_card.render_card(out_path=tmp_path / "c.jpg", number=7, genre=config.load_genres()[0],
                                 headline="とても長い見出しが二行にきれいに収まるかのテストです",
                                 short_name="名前", points=["a", "b", "c"], price=1000,
                                 review_average=4.2, review_count=10, product_image_url="http://x")
    assert Image.open(out).size == (1080, 1350)


def test_item_code_from_url():
    from sns_affiliate.rakuten import item_code_from_url
    assert item_code_from_url("https://item.rakuten.co.jp/shopA/item-123/?s-id=x") == "shopA:item-123"
    with pytest.raises(ValueError):
        item_code_from_url("https://www.amazon.co.jp/dp/xxx")


def test_prepare_from_inbox_photos(tmp_project, rakuten_on):
    folder = tmp_project / "inbox" / "イヤホン"
    folder.mkdir(parents=True)
    (folder / "memo.txt").write_text(
        "https://item.rakuten.co.jp/shopA/earphone1/\nジャンル: gadget\n通勤で毎日使ってる。電車の音がほぼ消える。\n",
        encoding="utf-8")
    for i, color in enumerate(["red", "green", "blue"], 1):
        Image.new("RGB", (1200, 900), color).save(folder / f"{i}.jpg")
    (folder / "10.jpg").write_bytes((folder / "1.jpg").read_bytes())  # 自然順で最後になる

    s = config.Settings()
    s.public_base_url = "https://example.github.io/sns-affiliate"
    writer = FakeWriter()
    post = pipeline.prepare(s, rakuten=FakeRakuten([]), writer=writer, rng=random.Random(0))

    assert post["source"] == "photo" and post["item_code"] == "shopA:earphone1"
    assert post["genre"] == "gadget"
    comment, photos = writer.calls[0]
    assert "電車の音" in comment and [p.name for p in photos] == ["1.jpg", "2.jpg", "3.jpg", "10.jpg"]
    # 表紙 + 写真3枚 + ポイントカード
    assert len(post["images"]) == 5
    for rel in post["images"]:
        assert Image.open(tmp_project / rel).size == (1080, 1350)
    # 使い終わった写真は削除、メモだけ残る
    assert not folder.exists()
    assert (tmp_project / "inbox" / "_used" / "0001_イヤホン.txt").exists()

    ig = FakeIG()
    pipeline.publish(s, ig=ig, wait=False)
    assert len(ig.calls[0][0]) == 5 and ig.calls[0][0][0].endswith("0001-01.jpg")


def _write_memo(root, name, text, photos=0):
    folder = root / "inbox" / name
    folder.mkdir(parents=True)
    (folder / "memo.txt").write_text(text, encoding="utf-8")
    for i in range(1, photos + 1):
        Image.new("RGB", (1000, 1000), "gray").save(folder / f"{i}.jpg")
    return folder


def test_inbox_single_without_photo(tmp_project, rakuten_on):
    _write_memo(tmp_project, "a", "https://item.rakuten.co.jp/shopA/x1/\n")
    s = config.Settings()
    post = pipeline.prepare(s, rakuten=FakeRakuten([]), writer=FakeWriter())
    assert post["kind"] == "single" and post["source"] == "pick"
    assert post["item_code"] == "shopA:x1" and len(post["images"]) == 1


def test_inbox_roundup_from_urls(tmp_project, rakuten_on):
    urls = "\n".join(f"https://item.rakuten.co.jp/shop{i}/item{i}/" for i in range(4))
    _write_memo(tmp_project, "b", f"テーマ: 機内持ち込みグッズ\nジャンル: travel\n{urls}\n", photos=2)
    s = config.Settings()
    writer = FakeWriter()
    post = pipeline.prepare(s, rakuten=FakeRakuten([]), writer=writer)

    assert post["kind"] == "roundup" and post["genre"] == "travel" and post["source"] == "pick"
    assert writer.calls[0] == ("roundup", "機内持ち込みグッズ", "", 4)
    assert [it["item_code"] for it in post["items"]] == [f"shop{i}:item{i}" for i in range(4)]
    assert len(post["images"]) == 6  # 表紙 + 4商品 + 比較表
    for rel in post["images"]:
        assert Image.open(tmp_project / rel).size == (1080, 1350)
    assert "①" in post["caption"] and "#001" in post["caption"] and post["caption"].startswith("【PR】")
    html = (tmp_project / "docs" / "index.html").read_text()
    assert "#001-1" in html and "#001-4" in html and "shop3:item3" in html


def test_auto_roundup_picks_distinct_shops_and_skips_recent(tmp_project):
    items = [make_item(f"s{i}:x", reviews=100 + i, shop=f"shop{i % 6}") for i in range(10)]
    s = config.Settings()
    post = pipeline.prepare(s, mode="roundup", rakuten=FakeRakuten(items), writer=FakeWriter(),
                            rng=random.Random(0))
    shops = [it["item_code"] for it in post["items"]]
    assert len(shops) == 5
    assert len({items[int(c[1:].split(":")[0])].shop_name for c in shops}) == 5
    prices = [it["price"] for it in post["items"]]
    assert prices == sorted(prices)
    # 次のまとめでは同じ商品を使わない
    post2 = pipeline.prepare(s, mode="roundup", genre_id=post["genre"], rakuten=FakeRakuten(items),
                             writer=FakeWriter(), rng=random.Random(0))
    assert not {it["item_code"] for it in post2["items"]} & set(shops)


def test_roundup_day():
    from datetime import datetime
    s = config.Settings()
    s.roundup_weekdays = "3,6"
    assert pipeline.is_roundup_day(s, datetime(2026, 11, 18))      # 水
    assert not pipeline.is_roundup_day(s, datetime(2026, 11, 16))  # 月


def test_roundup_caption_limit_and_parse_fallback():
    items = [make_item(f"c{i}") for i in range(3)]
    copy = parse_roundup({"title": "t", "subtitle": "s", "caption": "あ" * 5000, "hashtags": ["x"],
                          "items": [{"short_name": "a", "best_for": "b", "points": ["1", "2", "3"]}]}, items)
    assert len(copy.items) == 3 and copy.items[0].points == ["1", "2"]
    text = compose_roundup_caption(copy, items, 7)
    assert len(text) <= 2200 and "③" in text and "#PR" in text


A8_PROGRAMS = {"programs": [
    {"id": "trip", "name": "テスト旅行予約", "genre": "travel", "link": "https://px.a8.net/svt/ejp?a8mat=AAA",
     "impression": "https://www10.a8.net/0.gif?a8mat=AAA", "sns_allowed": True, "price_label": "会員登録無料",
     "facts": ["国内の宿を予約できる"], "ng_words": ["最安"]},
    {"id": "gym", "name": "テストジム", "genre": "training", "link": "https://px.a8.net/svt/ejp?a8mat=BBB",
     "sns_allowed": True, "facts": ["24時間営業"]},
    {"id": "nosns", "name": "SNS不可の案件", "genre": "gadget", "link": "https://px.a8.net/svt/ejp?a8mat=CCC",
     "sns_allowed": False, "facts": ["x"]},
]}


def _write_programs(root):
    path = root / "config" / "a8_programs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(A8_PROGRAMS, ensure_ascii=False), encoding="utf-8")


def test_a8_auto_rotates_programs_and_skips_non_sns(tmp_project):
    _write_programs(tmp_project)
    s = config.Settings()
    codes = []
    for _ in range(3):
        try:
            post = pipeline.prepare(s, mode="a8", writer=FakeWriter())
        except RuntimeError as e:
            assert "A8 案件がありません" in str(e)
            break
        codes.append(post["item_code"])
        assert post["network"] == "a8" and post["affiliate_url"].startswith("https://px.a8.net/")
        assert post["caption"].startswith("【PR】A8.net")
    # SNS 不可の案件は使わず、2件を順番に使ったあとはクールダウンで止まる
    assert sorted(codes) == ["a8:gym", "a8:trip"]
    html = (tmp_project / "docs" / "index.html").read_text()
    assert "公式サイトを見る" in html and "0.gif?a8mat=AAA" in html and "会員登録無料" in html


def test_a8_from_inbox_with_photos(tmp_project):
    _write_programs(tmp_project)
    _write_memo(tmp_project, "trip", "A8: trip\n先月これで温泉宿を予約した。ポイントが貯まった。\n", photos=2)
    writer = FakeWriter()
    post = pipeline.prepare(config.Settings(), writer=writer)
    assert post["item_code"] == "a8:trip" and post["genre"] == "travel" and post["source"] == "photo"
    assert writer.calls[0][0] == "a8" and "温泉宿" in writer.calls[0][2]
    assert len(post["images"]) == 3


def test_a8_inbox_rejects_non_sns_and_raw_links(tmp_project):
    _write_programs(tmp_project)
    _write_memo(tmp_project, "x", "A8: nosns\n")
    with pytest.raises(RuntimeError, match="sns_allowed"):
        pipeline.prepare(config.Settings(), writer=FakeWriter())


def test_a8_raw_link_in_memo_gives_help(tmp_project, rakuten_on):
    _write_memo(tmp_project, "y", "https://px.a8.net/svt/ejp?a8mat=ZZZ\n")
    with pytest.raises(RuntimeError, match="A8: 案件ID"):
        pipeline.prepare(config.Settings(), rakuten=FakeRakuten([]), writer=FakeWriter())


def test_generate_a8_retries_on_ng_words():
    from sns_affiliate.a8 import A8Program
    from sns_affiliate.caption import CaptionGenerator

    program = A8Program(id="p", name="n", genre="travel", link="https://px.a8.net/x", facts=["f"],
                        sns_allowed=True, ng_words=["最安"])
    replies = [{"headline": "業界最安の予約", "points": ["a", "b", "c"], "short_name": "n",
                "caption": "c", "hashtags": ["#a"], "slide_captions": []},
               {"headline": "お得に予約", "points": ["a", "b", "c"], "short_name": "n",
                "caption": "c", "hashtags": ["#a"], "slide_captions": []}]
    prompts = []

    class Gen(CaptionGenerator):
        def __init__(self):
            pass

        def _call(self, content, schema):
            prompts.append(content[-1]["text"])
            return replies[len(prompts) - 1]

    copy = Gen().generate_a8(program, config.load_genres()[1], 3)
    assert copy.headline == "お得に予約"
    assert "最安" in prompts[1]


def test_rakuten_request_has_new_api_auth_and_referer():
    from sns_affiliate.rakuten import RakutenClient

    captured = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"Items": [{"itemCode": "s:1", "itemName": "n", "itemPrice": 100, "itemUrl": "u",
                               "affiliateUrl": "a", "mediumImageUrls": ["https://x/img.jpg?_ex=128x128"]}]}

    class Session:
        def get(self, url, params, headers, timeout):
            captured.update(url=url, params=params, headers=headers)
            return Resp()

    s = config.Settings()
    s.rakuten_app_id, s.rakuten_access_key, s.rakuten_affiliate_id = "app", "pk_key", "aff"
    s.public_base_url = "https://kapibara1812.github.io/sns-affiliate"
    items = RakutenClient(s, session=Session()).search("イヤホン")
    assert captured["url"].startswith("https://openapi.rakuten.co.jp/")
    assert captured["params"]["accessKey"] == "pk_key" and captured["params"]["applicationId"] == "app"
    assert captured["headers"]["Origin"] == "https://kapibara1812.github.io"
    assert captured["headers"]["Referer"] == "https://kapibara1812.github.io/sns-affiliate/"
    assert items[0].image_url.endswith("_ex=600x600")


def test_rakuten_403_explains_allowed_websites():
    from sns_affiliate.rakuten import RakutenClient

    class Resp:
        status_code = 403
        text = "forbidden"

        def json(self):
            return {"errors": {"errorCode": 403, "errorMessage": "REFERRER_MISSING"}}

    class Session:
        def get(self, *a, **k):
            return Resp()

    s = config.Settings()
    s.rakuten_app_id, s.rakuten_access_key, s.rakuten_affiliate_id = "app", "pk", "aff"
    s.public_base_url = "https://kapibara1812.github.io/sns-affiliate"
    with pytest.raises(RuntimeError, match="許可されたWebサイト"):
        RakutenClient(s, session=Session()).search("x")


def test_ig_api_url_follows_token_type():
    s = config.Settings()
    s.ig_api_base = ""
    s.ig_access_token = "EAAUxxxx"
    assert config.ig_api_url(s) == "https://graph.facebook.com/v23.0"
    s.ig_access_token = "IGAAxxxx"
    assert config.ig_api_url(s) == "https://graph.instagram.com/v23.0"
    s.ig_api_base = "https://graph.facebook.com/v25.0/"
    assert config.ig_api_url(s) == "https://graph.facebook.com/v25.0"


def test_instagram_carousel_calls_facebook_graph_with_eaa_token():
    from sns_affiliate.instagram import InstagramClient

    calls = []

    class Resp:
        def __init__(self, data):
            self._data, self.status_code, self.content, self.text = data, 200, b"x", ""

        def json(self):
            return self._data

    class Session:
        def request(self, method, url, params, timeout):
            calls.append((method, url, dict(params)))
            if url.endswith("/media") and params.get("media_type") == "CAROUSEL":
                return Resp({"id": "parent"})
            if url.endswith("/media"):
                return Resp({"id": f"child{len(calls)}"})
            if url.endswith("/media_publish"):
                return Resp({"id": "m1"})
            if "fields" in params and "status_code" in params["fields"]:
                return Resp({"status_code": "FINISHED"})
            return Resp({"permalink": "https://instagram.com/p/abc"})

    s = config.Settings()
    s.ig_api_base, s.ig_user_id, s.ig_access_token = "", "17841400000000000", "EAAUtoken"
    out = InstagramClient(s, session=Session(), poll_interval=0).publish(["https://a/1.jpg", "https://a/2.jpg"], "cap")
    assert out == {"id": "m1", "permalink": "https://instagram.com/p/abc", "timestamp": None}
    assert all(url.startswith("https://graph.facebook.com/v23.0/") for _, url, _ in calls)
    parent = [c for c in calls if c[2].get("media_type") == "CAROUSEL"][0]
    assert parent[2]["children"].count(",") == 1 and parent[2]["caption"] == "cap"


def _no_rakuten(s):
    s.rakuten_app_id = s.rakuten_access_key = s.rakuten_affiliate_id = ""
    return s


def test_a8_only_when_rakuten_not_configured(tmp_project):
    _write_programs(tmp_project)
    s = _no_rakuten(config.Settings())
    # A8 曜日でなくても、楽天が未設定なら A8 案件を使う（is_a8_day はフィクスチャで False）
    post = pipeline.prepare(s, writer=FakeWriter())
    assert post["network"] == "a8"
    post2 = pipeline.prepare(s, writer=FakeWriter())
    assert {post["item_code"], post2["item_code"]} == {"a8:trip", "a8:gym"}
    # 使える案件がクールダウン中なら、エラーではなく「お休み」
    with pytest.raises(pipeline.NothingToPost, match="直近"):
        pipeline.prepare(s, writer=FakeWriter())


def test_nothing_to_post_without_rakuten_or_a8(tmp_project):
    s = _no_rakuten(config.Settings())
    with pytest.raises(pipeline.NothingToPost, match="A8 案件"):
        pipeline.prepare(s, writer=FakeWriter())


def test_cli_prepare_rests_quietly(tmp_project, monkeypatch, capsys):
    from sns_affiliate import __main__ as cli

    for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "RAKUTEN_AFFILIATE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert cli.main(["prepare"]) == 0
    assert "お休み" in capsys.readouterr().out


def test_a8_explainer_carousel_with_official_image(tmp_project, monkeypatch):
    from sns_affiliate import image_card

    _write_programs(tmp_project)
    data = json.loads((tmp_project / "config" / "a8_programs.json").read_text())
    data["programs"][0]["images"] = ["https://www10.a8.net/svt/bgt?banner"]
    (tmp_project / "config" / "a8_programs.json").write_text(json.dumps(data, ensure_ascii=False))
    monkeypatch.setattr(image_card, "_fetch_image", lambda url: Image.new("RGB", (300, 250), "orange"))

    post = pipeline.prepare(config.Settings(), mode="a8", genre_id="travel", writer=FakeWriter())
    assert post["format"] == "explainer" and post["item_code"] == "a8:trip"
    # 表紙・悩み・比較・手順・公式画像・ポイント・こんな人に・まとめ
    assert len(post["images"]) == 8
    names = [Path(p).name for p in post["images"]]
    assert names == [f"0001-{i:02d}.jpg" for i in range(1, 9)]
    for rel in post["images"]:
        assert Image.open(tmp_project / rel).size == (1080, 1350)


def test_a8_explainer_skips_missing_parts(tmp_project, monkeypatch):
    from sns_affiliate import image_card
    from sns_affiliate.caption import parse_explainer

    ex = parse_explainer({"cover_hook": "h", "cover_sub": "s", "short_name": "n", "pains_title": "", "pains": ["a"],
                          "compare": {"title": "", "columns": ["x"], "rows": []}, "steps_title": "", "steps": [],
                          "features": ["f1", "f2"], "fit": [], "caption": "c", "hashtags": ["t"]})
    assert ex.compare_rows == [] and ex.steps == [] and ex.pains_title
    monkeypatch.setattr(image_card, "_fetch_image", lambda url: None)  # 公式画像が取れない
    from sns_affiliate.a8 import A8Program
    prog = A8Program(id="p", name="n", genre="travel", link="https://px.a8.net/x", facts=["f"], sns_allowed=True,
                     images=["https://x/broken.png"])
    slides = pipeline._render_explainer(config.Settings(), 9, config.load_genres()[0], prog, ex)
    assert [Path(p).name for p in slides] == [f"0009-{i:02d}.jpg" for i in range(1, 5)]  # 表紙・悩み・ポイント・まとめ


def test_reel_is_vertical_mp4(tmp_path):
    from sns_affiliate.reel import build_reel, probe

    slides = []
    for i, c in enumerate(["red", "green", "blue"]):
        f = tmp_path / f"{i}.jpg"
        Image.new("RGB", (1080, 1350), c).save(f)
        slides.append(f)
    out = build_reel(slides, tmp_path / "r.mp4", genre=config.load_genres()[0], account_name="yoku_kapi",
                     seconds=1.0, cover_seconds=1.0, fade=0.3)
    info = probe(out)
    assert out.stat().st_size > 1000
    assert "1080x1920" in info and "h264" in info and "aac" in info
    dur = [l for l in info.splitlines() if "Duration" in l][0]
    assert "00:00:03" in dur  # 1+1+1+0.3 秒


def test_reels_are_attached_and_published_next(tmp_project, monkeypatch):
    _write_programs(tmp_project)
    monkeypatch.setenv("MAKE_REELS", "true")
    s = config.Settings()
    s.public_base_url = "https://example.github.io/sns-affiliate"
    calls = {}
    monkeypatch.setattr("sns_affiliate.reel.build_reel",
                        lambda slides, out, **k: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b"mp4"), out)[2])
    post = pipeline.prepare(s, mode="a8", genre_id="travel", writer=FakeWriter())
    assert post["reel_status"] == "pending" and post["reel_path"].endswith("0001.mp4")

    class IG(FakeIG):
        def publish_reel(self, url, caption, cover_url=None):
            calls["url"] = url
            return {"id": "r1", "permalink": "https://instagram.com/reel/x"}

    ig = IG()
    assert pipeline.publish_reels(s, ig=ig, wait=False) == []  # カルーセルがまだ未投稿なら待つ
    pipeline.publish(s, ig=ig, wait=False)
    done = pipeline.publish_reels(s, ig=ig, wait=False)
    assert done[0]["reel_status"] == "published"
    assert calls["url"] == "https://example.github.io/sns-affiliate/videos/0001.mp4"


def test_prune_videos_removes_old_files(tmp_project):
    video = tmp_project / "docs" / "videos" / "0001.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")
    posts = [{"reel_path": "docs/videos/0001.mp4", "reel_status": "published",
              "reel_published_at": "2026-01-01T00:00:00+09:00", "created_at": "2026-01-01T00:00:00+09:00"},
             {"reel_path": "docs/videos/0002.mp4", "reel_status": "pending", "created_at": "2026-01-01T00:00:00+09:00"}]
    assert pipeline.prune_videos(posts, 7) == 1
    assert not video.exists() and posts[0]["reel_path"] is None and posts[1]["reel_path"]


# ---------------------------------------------------------------------------
# 下書きの流れ（① 調査 → ③ まとめて作る → 承認 → 投稿 → ④ 作り直し）
# ---------------------------------------------------------------------------

def _only_training_supplement():
    genres = json.loads(config.GENRES_FILE.read_text(encoding="utf-8"))
    for g in genres["genres"]:
        g["enabled"] = g["id"] in {"training", "supplement"}
    config.GENRES_FILE.write_text(json.dumps(genres, ensure_ascii=False), encoding="utf-8")


def test_research_csv_to_patterns(tmp_project):
    from sns_affiliate import research

    research.RESEARCH_DIR.mkdir(parents=True)
    research.POSTS_CSV.write_text(
        "アカウント名,投稿URL,種類(カルーセル/リール),いいね数,保存数,再生数,1枚目(表紙)に書かれた見出しの文字,キャプションの1行目\n"
        "a,u,カルーセル,1.2万,3000,,プロテイン選びで損してない？,知らないと損\n"
        "b,u,リール,800,10,5万,宅トレ初心者が買うべき3つ,最初はこれだけ\n"
        "c,u,リール,5,0,,,\n", encoding="utf-8-sig")
    rows = research.load_posts_csv()
    assert len(rows) == 2 and rows[0]["likes"] == 12000
    assert research.rows_to_text(rows).startswith("1. [カルーセル] いいね12000 保存3000")
    w = FakeWriter()
    result = research.analyze(w, "トレーニンググッズ")
    assert result["source_count"] == 2 and len(research.load_patterns()) == 5
    assert "型0" in research.PATTERNS_MD.read_text(encoding="utf-8")


def test_draft_markdown_roundtrip(tmp_project):
    from sns_affiliate import drafts as dr
    from sns_affiliate.caption import ExplainerCopy

    copy = ExplainerCopy(cover_hook="プロテイン選びで損してない？", cover_sub="サブ", short_name="GORILLA SQUAD",
                         pains_title="悩み", pains=["a", "b"], compare_title="比較", compare_columns=["X", "Y", "Z"],
                         compare_rows=[("手間", ["△", "◎", "○"]), ("価格", ["○", "△", "◎"])], steps_title="手順",
                         steps=[("買う", "公式で"), ("使う", "毎日")], features=["f1", "f2"], fit=["初心者"],
                         caption="1行目\n2行目", hashtags=["#筋トレ", "#宅トレ"])
    d = dr.Draft(id=7, kind="a8", genre_id="training", subject="gym", name="テストジム", copy=copy,
                 link="https://px.a8.net/x", price_label="月額1,000円〜", pattern="損を突く", audience="初心者")
    path = dr.save(d)
    back = dr.from_markdown(path.read_text(encoding="utf-8"), path)
    assert back.id == 7 and back.kind == "a8" and not back.approved
    assert back.copy == copy and back.link == d.link and back.price_label == d.price_label
    # 人が直して承認する
    text = path.read_text(encoding="utf-8").replace("承認: いいえ", "承認: はい").replace("大見出し: プロテイン選びで損してない？",
                                                                                   "大見出し: その選び方、損してるかも")
    path.write_text(text, encoding="utf-8")
    edited = dr.from_markdown(path.read_text(encoding="utf-8"), path)
    assert edited.approved and edited.copy.cover_hook == "その選び方、損してるかも"


def test_generate_drafts_mix_and_preview_page(tmp_project):
    _only_training_supplement()
    _write_programs(tmp_project)  # gym（training）が使える
    s = config.Settings()
    s.repo_slug = "owner/repo"
    w = FakeWriter()
    made = pipeline.generate_drafts(s, count=10, writer=w, rng=random.Random(0))
    kinds = [d.kind for d in made]
    assert len(made) == 10 and kinds.count("tips") >= 3 and "a8" in kinds
    assert {d.genre_id for d in made} == {"training", "supplement"}
    assert sum(1 for d in made if d.subject == "gym") <= 2  # 同じ案件ばかりにしない
    assert (tmp_project / "drafts" / "0001.md").exists()
    html = (tmp_project / "docs" / "drafts" / "index.html").read_text(encoding="utf-8")
    assert "承認待ち 10" in html and "https://github.com/owner/repo/edit/main/drafts/0001.md" in html
    assert list((tmp_project / "docs" / "drafts" / "0001").glob("*.jpg"))


def test_only_approved_drafts_are_published(tmp_project):
    from sns_affiliate import drafts as dr

    _only_training_supplement()
    _write_programs(tmp_project)
    s = config.Settings()
    s.public_base_url = "https://example.github.io/sns-affiliate"
    made = pipeline.generate_drafts(s, count=3, writer=FakeWriter(), rng=random.Random(0), render_previews=False)
    with pytest.raises(pipeline.NothingToPost):
        pipeline.publish_draft(s)  # 承認するまで投稿されない
    target = made[1]
    target.approved = True
    dr.save(target)
    post = pipeline.publish_draft(s)
    assert post["draft_id"] == target.id and post["number"] == 1
    assert all(Path(p).name.startswith("0001-") for p in post["images"])
    if target.kind == "tips":
        assert "【PR】" not in post["caption"] and post["network"] == "none"
    else:
        assert post["caption"].startswith("【PR】") and "#001" in post["caption"]
    assert not (tmp_project / "drafts" / target.filename).exists()
    done = dr.from_markdown((tmp_project / "drafts" / "_done" / target.filename).read_text(encoding="utf-8"))
    assert done.state == "投稿済み #001"
    with pytest.raises(pipeline.NothingToPost):
        pipeline.publish_draft(s)


def test_scheduled_draft_waits_for_its_day(tmp_project):
    from datetime import date
    from sns_affiliate import drafts as dr

    _only_training_supplement()
    made = pipeline.generate_drafts(config.Settings(), count=2, writer=FakeWriter(), rng=random.Random(0),
                                    render_previews=False)
    made[0].approved, made[0].scheduled = True, "2099-01-01"
    made[1].approved = True
    dr.save(made[0]); dr.save(made[1])
    assert dr.next_approved(date(2026, 11, 21)).id == made[1].id
    made[1].approved = False
    dr.save(made[1])
    assert dr.next_approved(date(2026, 11, 21)) is None
    assert dr.next_approved(date(2099, 1, 1)).id == made[0].id


def test_tips_posts_are_not_listed_on_link_page(tmp_project):
    from sns_affiliate import drafts as dr

    _only_training_supplement()
    made = pipeline.generate_drafts(config.Settings(), count=4, writer=FakeWriter(), rng=random.Random(0),
                                    render_previews=False)
    tips = next(d for d in made if d.kind == "tips")
    tips.approved = True
    dr.save(tips)
    post = pipeline.publish_draft(config.Settings())
    assert post["network"] == "none"
    html = (tmp_project / "docs" / "index.html").read_text(encoding="utf-8")
    assert 'class="item"' not in html


def test_remix_hits_after_a_week(tmp_project):
    from sns_affiliate import drafts as dr

    _only_training_supplement()
    _write_programs(tmp_project)
    s = config.Settings()
    made = pipeline.generate_drafts(s, count=1, writer=FakeWriter(), rng=random.Random(0), render_previews=False)
    made[0].approved = True
    dr.save(made[0])
    post = pipeline.publish_draft(s)
    posts = storage.load_posts()
    posts[0].update(status="published", published_at="2026-01-01T20:00:00+09:00", insights={"saved": 42})
    storage.save_posts(posts)
    w = FakeWriter()
    remixed = pipeline.remix_hits(s, writer=w, render_previews=False)
    assert len(remixed) == 1 and remixed[0].remix_of == post["number"] and not remixed[0].approved
    assert w.calls[-1][-1] is True  # 前回の投稿を渡して作り直している
    assert pipeline.remix_hits(s, writer=FakeWriter(), render_previews=False) == []  # 同じ投稿は1回だけ


def test_genre_ng_words_trigger_regeneration():
    from sns_affiliate.caption import CaptionGenerator

    replies = [
        {"cover_hook": "飲むだけで痩せるプロテイン", "cover_sub": "s", "short_name": "n", "pains_title": "t", "pains": ["a"],
         "compare": {"title": "", "columns": [], "rows": []}, "steps_title": "", "steps": [], "features": ["f"],
         "fit": ["x"], "caption": "c", "hashtags": ["#a"]},
        {"cover_hook": "プロテイン、味で選んでる？", "cover_sub": "s", "short_name": "n", "pains_title": "t", "pains": ["a"],
         "compare": {"title": "", "columns": [], "rows": []}, "steps_title": "", "steps": [], "features": ["f"],
         "fit": ["x"], "caption": "c", "hashtags": ["#a"]},
    ]
    prompts = []

    class Gen(CaptionGenerator):
        def __init__(self):
            pass

        def _call(self, content, schema):
            prompts.append(content[-1]["text"])
            return replies[len(prompts) - 1]

    genre = {g["id"]: g for g in config.load_genres(include_disabled=True)}["supplement"]
    copy = Gen().generate_draft({"kind": "tips", "name": "プロテインの選び方"}, genre,
                                {"name": "型", "template": "t"}, "初心者")
    assert copy.cover_hook == "プロテイン、味で選んでる？"
    assert "痩せる" in prompts[1] and "薬機法" in prompts[0]


# ---------------------------------------------------------------------------
# AI 画像（OpenAI）
# ---------------------------------------------------------------------------

class FakeImageGen:
    """OpenAI の代わりに単色の画像を作る。呼ばれた回数を数える."""

    enabled = True

    def __init__(self):
        self.calls = []

    def generate(self, prompt, out_path, kind="cover"):
        self.calls.append((kind, prompt))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        size = (1024, 1024) if kind == "cover" else (1536, 1024)
        Image.new("RGB", size, "lightblue" if kind == "cover" else "pink").save(out_path)
        return out_path


def test_imagegen_calls_openai_and_saves_png(tmp_path):
    import base64
    import io
    from sns_affiliate.imagegen import ImageGenerator

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    sent = {}

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(buf.getvalue()).decode()}]}

    class Session:
        def post(self, url, json, headers, timeout):
            sent.update(url=url, body=json, headers=headers)
            return Resp()

    s = config.Settings()
    s.openai_api_key, s.image_quality, s.image_model = "sk-test", "low", "gpt-image-2"
    out = ImageGenerator(s, session=Session()).generate("A protein shaker on a bench", tmp_path / "a.png", "scene")
    assert out and Image.open(out).size == (8, 8)
    assert sent["url"].endswith("/v1/images/generations") and sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["body"]["size"] == "1536x1024" and sent["body"]["quality"] == "low"
    assert "No text" in sent["body"]["prompt"] and "No realistic humans" in sent["body"]["prompt"]
    s.openai_api_key = ""
    assert ImageGenerator(s, session=Session()).generate("x", tmp_path / "b.png") is None  # キーがなければ作らない


def test_draft_ai_images_are_cached_and_regenerated_on_edit(tmp_project):
    from sns_affiliate import drafts as dr

    _only_training_supplement()
    gen = FakeImageGen()

    class W(FakeWriter):
        def generate_draft(self, *a, **k):
            c = super().generate_draft(*a, **k)
            c.cover_image_prompt = "A protein shaker and a towel"
            c.scene_image_prompt = "A cozy home gym corner"
            return c

    s = config.Settings()
    made = pipeline.generate_drafts(s, count=1, writer=W(), rng=random.Random(0), image_gen=gen)
    d = made[0]
    assert [k for k, _ in gen.calls] == ["cover", "scene"]
    slides = sorted((tmp_project / "docs" / "drafts" / f"{d.id:04d}").glob("*.jpg"))
    assert len(slides) >= 4
    # 同じ指示文なら作り直さない
    dr.render_preview(d, gen=gen)
    assert len(gen.calls) == 2
    # 表紙の指示文を直したら、表紙だけ作り直す
    text = d.path.read_text(encoding="utf-8").replace("表紙: A protein shaker and a towel", "表紙: Dumbbells on a mat")
    d.path.write_text(text, encoding="utf-8")
    edited = dr.from_markdown(d.path.read_text(encoding="utf-8"), d.path)
    dr.render_preview(edited, gen=gen)
    assert gen.calls[-1] == ("cover", "Dumbbells on a mat") and len(gen.calls) == 3
    assert len(list((tmp_project / "drafts" / "images").glob(f"{d.id:04d}-cover-*.png"))) == 1
    # 投稿するとキャプションに AI 画像の注記が入る
    edited.approved = True
    dr.save(edited)
    post = pipeline.publish_draft(s, image_gen=gen)
    assert "AIで作成したイメージ" in post["caption"] and len(gen.calls) == 3


def test_rakuten_switch_off_never_touches_rakuten(tmp_project, monkeypatch):
    monkeypatch.setenv("RAKUTEN_ENABLED", "false")
    _only_training_supplement()
    _write_programs(tmp_project)
    _write_memo(tmp_project, "z", "https://item.rakuten.co.jp/shopA/x1/\n")

    class Boom:
        def search(self, *a, **k):
            raise AssertionError("楽天にアクセスしてはいけない")

        lookup = search

    s = config.Settings()
    assert not config.rakuten_ready(s)
    # 自動投稿: 楽天のネタ（inbox）は後回しにして、A8 案件を使う
    post = pipeline.prepare(s, writer=FakeWriter(), rakuten=Boom())
    assert post["network"] == "a8" and (tmp_project / "inbox" / "z").exists()
    # 下書き作り: 楽天の商品を探しに行かない
    made = pipeline.generate_drafts(s, count=4, writer=FakeWriter(), rng=random.Random(0), render_previews=False)
    assert all(d.kind in ("a8", "tips") for d in made)
