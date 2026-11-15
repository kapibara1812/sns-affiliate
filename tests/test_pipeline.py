import json
import random

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

    def generate_a8(self, program, genre, number, comment="", photos=None):
        self.calls.append(("a8", program.id, comment))
        return self.generate(None, genre, number, comment, photos)

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


def test_prepare_from_inbox_photos(tmp_project):
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


def test_inbox_single_without_photo(tmp_project):
    _write_memo(tmp_project, "a", "https://item.rakuten.co.jp/shopA/x1/\n")
    s = config.Settings()
    post = pipeline.prepare(s, rakuten=FakeRakuten([]), writer=FakeWriter())
    assert post["kind"] == "single" and post["source"] == "pick"
    assert post["item_code"] == "shopA:x1" and len(post["images"]) == 1


def test_inbox_roundup_from_urls(tmp_project):
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


def test_a8_raw_link_in_memo_gives_help(tmp_project):
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
