import json
import random

import pytest
from PIL import Image

from sns_affiliate import config, pipeline, storage
from sns_affiliate.caption import PostCopy, compose_caption, parse_copy
from sns_affiliate.rakuten import Item


def make_item(code="shop:1", reviews=100, avg=4.5, price=3980):
    return Item(item_code=code, name=f"商品{code}", price=price, item_url="https://item.rakuten.co.jp/x",
                affiliate_url=f"https://hb.afl.rakuten.co.jp/{code}", image_url="", review_average=avg,
                review_count=reviews, shop_name="shop", caption="説明")


class FakeRakuten:
    def __init__(self, items):
        self.items = items
        self.keywords = []

    def search(self, keyword, **_):
        self.keywords.append(keyword)
        return self.items


class FakeWriter:
    def generate(self, item, genre, number):
        return PostCopy(headline="見出しテスト", points=["a", "b", "c"], short_name="短い名前",
                        caption=f"本文 #{number:03d}", hashtags=["#テスト"])


class FakeIG:
    def __init__(self):
        self.calls = []

    def publish_image(self, url, caption):
        self.calls.append((url, caption))
        return {"id": "m1", "permalink": "https://instagram.com/p/x"}


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POSTS_FILE", tmp_path / "data" / "posts.json")
    monkeypatch.setattr(config, "INSIGHTS_CSV", tmp_path / "data" / "insights.csv")
    monkeypatch.setattr(config, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(config, "IMAGES_DIR", tmp_path / "docs" / "images")
    monkeypatch.setattr(config, "ROOT", tmp_path)
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
    post = pipeline.prepare(s, rakuten=rakuten, writer=FakeWriter(), rng=random.Random(1))

    assert post["number"] == 1 and post["status"] == "prepared"
    img = Image.open(tmp_project / post["image_path"])
    assert img.size == (1080, 1350)
    html = (tmp_project / "docs" / "index.html").read_text()
    assert post["affiliate_url"] in html and 'rel="sponsored noopener"' in html

    # 同じ商品は連続で選ばれない
    post2 = pipeline.prepare(s, rakuten=rakuten, writer=FakeWriter(), rng=random.Random(1))
    assert post2["item_code"] != post["item_code"]

    ig = FakeIG()
    done = pipeline.publish(s, ig=ig, wait=False, limit=5)
    assert [p["status"] for p in done] == ["published", "published"]
    assert ig.calls[0][0] == "https://example.github.io/sns-affiliate/images/0001.jpg"
    assert ig.calls[0][1].startswith("【PR】")


def test_card_with_product_image(tmp_path, monkeypatch):
    from sns_affiliate import image_card
    monkeypatch.setattr(image_card, "_fetch_image", lambda url: Image.new("RGB", (600, 400), "red"))
    out = image_card.render_card(out_path=tmp_path / "c.jpg", number=7, genre=config.load_genres()[0],
                                 headline="とても長い見出しが二行にきれいに収まるかのテストです",
                                 short_name="名前", points=["a", "b", "c"], price=1000,
                                 review_average=4.2, review_count=10, product_image_url="http://x")
    assert Image.open(out).size == (1080, 1350)
