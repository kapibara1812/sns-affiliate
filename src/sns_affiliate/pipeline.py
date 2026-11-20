"""商品選定 → 投稿文生成 → 画像生成 → 公開 → 計測 のパイプライン."""

from __future__ import annotations

import csv
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import a8, config, inbox, storage
from .caption import (CaptionGenerator, compose_a8_caption, compose_a8_explainer_caption, compose_caption,
                      compose_roundup_caption)
from .image_card import (render_card, render_compare_slide, render_cta_slide, render_explainer_cover,
                         render_list_slide, render_official_image_slide, render_photo_cover, render_photo_slide,
                         render_roundup_cover, render_roundup_item, render_roundup_summary, render_steps_slide)
from .instagram import InstagramClient
from .rakuten import Item, RakutenClient, item_code_from_url
from .site import build_site


class NothingToPost(RuntimeError):
    """今日は投稿できるネタがない（エラーではなく、静かに休む）."""


def pick_genre(genres: list[dict], number: int, genre_id: str | None = None) -> dict:
    """genre_id の指定があればそのジャンル（止めているジャンルでも可）、なければ有効なジャンルを順番に."""
    if genre_id:
        for g in genres + config.load_genres(include_disabled=True):
            if g["id"] == genre_id:
                return g
        raise ValueError(f"ジャンル {genre_id} は config/genres.json にありません")
    return genres[(number - 1) % len(genres)]


def choose_item(items: list[Item], exclude: set[str], settings: config.Settings,
                rng: random.Random) -> Item | None:
    candidates = [
        it for it in items
        if it.item_code not in exclude
        and it.review_count >= settings.min_review_count
        and it.review_average >= settings.min_review_average
    ]
    if not candidates:
        return None
    # レビュー件数×評価で上位から少しランダムに選び、毎回同じ商品にならないようにする
    candidates.sort(key=lambda it: it.review_count * it.review_average, reverse=True)
    return rng.choice(candidates[:5])


def _is_weekday(spec: str, today: datetime | None = None) -> bool:
    today = today or datetime.now(storage.JST)
    days = {int(d) for d in spec.replace(" ", "").split(",") if d}
    return today.isoweekday() in days


def is_roundup_day(settings: config.Settings, today: datetime | None = None) -> bool:
    return _is_weekday(settings.roundup_weekdays, today)


def is_a8_day(settings: config.Settings, today: datetime | None = None) -> bool:
    return _is_weekday(settings.a8_weekdays, today)


def prepare(settings: config.Settings, *, genre_id: str | None = None, use_inbox: bool = True,
            mode: str = "auto", rakuten: RakutenClient | None = None, writer: CaptionGenerator | None = None,
            rng: random.Random | None = None, inbox_dir: Path | None = None) -> dict:
    """次の投稿を1件準備し、posts.json と一覧ページを更新する.

    優先順位:
      1. inbox/ に置かれたネタ（写真カルーセル / 指定商品の単品 / 指定商品のまとめ）
      2. A8 案件（mode="a8" か A8 曜日で、使える案件がある場合）
      3. 自動選定（mode="roundup" か まとめ曜日ならまとめ投稿、それ以外は単品）
    """
    if mode not in {"auto", "single", "roundup", "a8"}:
        raise ValueError(f"mode は auto / single / roundup / a8 のいずれかです: {mode}")
    rng = rng or random.Random()
    genres = config.load_genres()
    posts = storage.load_posts()
    number = storage.next_number(posts)
    writer = writer or CaptionGenerator(settings.claude_model)
    exclude = storage.recently_posted_codes(posts, settings.repost_cooldown_days)
    programs = a8.load_programs()

    entries = inbox.list_entries(inbox_dir) if use_inbox else []
    entry = entries[0] if entries else None
    program = None
    a8_only = mode == "auto" and not config.rakuten_ready(settings)  # 楽天が未設定の間は A8 だけで運用
    if entry is None and (mode == "a8" or a8_only or (mode == "auto" and is_a8_day(settings))):
        enabled_ids = {g["id"] for g in genres}
        candidates = [pr for pr in programs if genre_id or pr.genre in enabled_ids]
        program = a8.pick_program(candidates, posts, settings.a8_cooldown_days, genre_id)
        if program is None and a8_only:
            if not a8.usable(candidates):
                raise NothingToPost("楽天のキーが未設定で、使える A8 案件（sns_allowed: true）もまだ登録されていないため、今日は投稿しません")
            raise NothingToPost(f"A8 案件はすべて直近 {settings.a8_cooldown_days} 日以内に投稿済みのため、今日は投稿しません"
                                "（案件を増やすか、A8_COOLDOWN_DAYS を短くしてください）")
        if program is None and mode == "a8":
            raise RuntimeError("投稿できる A8 案件がありません（sns_allowed: true の案件を config/a8_programs.json に登録してください）")

    if entry and entry.a8_id:
        program = a8.get_program(programs, entry.a8_id)
        genre = pick_genre(genres, number, genre_id or entry.genre_id or program.genre)
        post = _build_a8(settings, writer, number, genre, program, entry.comment, entry.photos,
                         source="photo" if entry.photos else "pick")
    elif program is not None:
        genre = pick_genre(genres, number, genre_id or program.genre)
        post = _build_a8(settings, writer, number, genre, program, "", [], source="auto")
    elif entry:
        rakuten = rakuten or RakutenClient(settings)
        genre = pick_genre(genres, number, genre_id or entry.genre_id)
        items = [_lookup(rakuten, url, entry.name) for url in entry.urls]
        if entry.is_roundup:
            post = _build_roundup(settings, writer, number, genre, items, entry.theme or genre["label"] + "のおすすめ",
                                  entry.comment, source="pick")
        else:
            post = _build_single(settings, writer, number, genre, items[0], entry.comment, entry.photos,
                                 source="photo" if entry.photos else "pick")
    else:
        rakuten = rakuten or RakutenClient(settings)
        genre = pick_genre(genres, number, genre_id)
        roundup = mode == "roundup" or (mode == "auto" and is_roundup_day(settings))
        if roundup:
            items, keyword = find_items(rakuten, genre, exclude, settings, rng, settings.roundup_size)
            post = _build_roundup(settings, writer, number, genre, items, keyword, "", source="auto")
        else:
            item = find_item(rakuten, genre, exclude, settings, rng)
            post = _build_single(settings, writer, number, genre, item, "", [], source="auto")

    if settings.make_reels and len(post["images"]) >= 3:
        _attach_reel(settings, post, genre)

    posts.append(post)
    storage.save_posts(posts)
    if entry:
        inbox.mark_used(entry, number)
    build_site(posts, genres, settings.site_title)
    return post


def _attach_reel(settings: config.Settings, post: dict, genre: dict) -> None:
    """カルーセルと同じ内容のリール動画を作り、翌日の投稿待ちにする（失敗してもカルーセルは投稿する）."""
    from .reel import build_reel

    out = config.VIDEOS_DIR / f"{post['number']:04d}.mp4"
    try:
        build_reel([config.ROOT / p for p in post["images"]], out, genre=genre, account_name=settings.account_name)
    except Exception as e:  # noqa: BLE001
        post["reel_status"] = "failed"
        post["reel_error"] = str(e)[:300]
        return
    post["reel_path"] = str(out.relative_to(config.ROOT))
    post["reel_status"] = "pending"


def _lookup(rakuten: RakutenClient, url: str, entry_name: str) -> Item:
    if "a8.net" in url:
        raise RuntimeError(f"inbox/{entry_name}: A8.net のリンクは config/a8_programs.json に案件として登録し、"
                           "memo.txt には「A8: 案件ID」と書いてください")
    try:
        item = rakuten.lookup(item_code_from_url(url))
    except ValueError as e:
        raise RuntimeError(f"inbox/{entry_name}: {e}") from e
    if item is None:
        raise RuntimeError(f"inbox/{entry_name}: 楽天で商品が見つかりません（URLを確認してください）: {url}")
    time.sleep(1.1)  # 楽天APIのレート制限対策
    return item


def _item_record(item: Item, short_name: str, image: Path) -> dict:
    return {
        "item_code": item.item_code,
        "item_name": item.name,
        "short_name": short_name,
        "price": item.price,
        "review_average": item.review_average,
        "review_count": item.review_count,
        "item_url": item.item_url,
        "affiliate_url": item.affiliate_url,
        "image": str(image.relative_to(config.ROOT)),
    }


def _out(number: int, i: int | None = None) -> Path:
    name = f"{number:04d}.jpg" if i is None else f"{number:04d}-{i:02d}.jpg"
    return config.IMAGES_DIR / name


def _build_single(settings: config.Settings, writer: CaptionGenerator, number: int, genre: dict, item: Item,
                  comment: str, photos: list[Path], source: str) -> dict:
    copy = writer.generate(item, genre, number, comment, photos)
    card_kwargs = dict(number=number, genre=genre, headline=copy.headline, short_name=copy.short_name,
                       points=copy.points, price=item.price, review_average=item.review_average,
                       review_count=item.review_count, account_name=settings.account_name)
    images: list[Path] = []
    if photos:
        images.append(render_photo_cover(photo=photos[0], out_path=_out(number, 1), number=number, genre=genre,
                                         headline=copy.headline, short_name=copy.short_name))
        for i, (photo, telop) in enumerate(zip(photos[1:], copy.slide_captions), start=2):
            images.append(render_photo_slide(photo=photo, out_path=_out(number, i), caption=telop, genre=genre))
        images.append(render_card(out_path=_out(number, len(images) + 1), **card_kwargs))
    else:
        images.append(render_card(out_path=_out(number),
                                  product_image_url=item.image_url if settings.use_product_image else None,
                                  **card_kwargs))
    record = _item_record(item, copy.short_name, images[0])
    rel = [str(p.relative_to(config.ROOT)) for p in images]
    return {
        "number": number,
        "kind": "single",
        "genre": genre["id"],
        "source": source,
        **{k: v for k, v in record.items() if k != "image"},
        "image_path": rel[0],
        "images": rel,
        "headline": copy.headline,
        "points": copy.points,
        "caption": compose_caption(copy, item),
        "status": "prepared",
        "created_at": storage.now_iso(),
    }


def _build_a8(settings: config.Settings, writer: CaptionGenerator, number: int, genre: dict,
              program: a8.A8Program, comment: str, photos: list[Path], source: str) -> dict:
    if photos:  # 自分で撮った写真がある（実際に使った）場合は写真カルーセル
        copy = writer.generate_a8(program, genre, number, comment, photos)
        card_kwargs = dict(number=number, genre=genre, headline=copy.headline, short_name=copy.short_name,
                           points=copy.points, price=0, review_average=0, review_count=0,
                           account_name=settings.account_name,
                           price_label=program.price_label or "公式サイトをチェック",
                           cta_verb="詳細は", footnote="※条件・料金は公式サイトでご確認ください")
        images = [render_photo_cover(photo=photos[0], out_path=_out(number, 1), number=number, genre=genre,
                                     headline=copy.headline, short_name=copy.short_name)]
        for i, (photo, telop) in enumerate(zip(photos[1:], copy.slide_captions), start=2):
            images.append(render_photo_slide(photo=photo, out_path=_out(number, i), caption=telop, genre=genre))
        images.append(render_card(out_path=_out(number, len(images) + 1), **card_kwargs))
        headline, short_name, points = copy.headline, copy.short_name, copy.points
        caption = compose_a8_caption(copy, program)
        fmt = "photo"
    else:  # 写真がない場合は、保存されやすい図解カルーセル
        ex = writer.generate_a8_explainer(program, genre, number)
        images = _render_explainer(settings, number, genre, program, ex)
        headline, short_name, points = ex.cover_hook, ex.short_name, ex.features
        caption = compose_a8_explainer_caption(ex, program)
        fmt = "explainer"
    rel = [str(p.relative_to(config.ROOT)) for p in images]
    return {
        "number": number,
        "kind": "single",
        "format": fmt,
        "network": "a8",
        "genre": genre["id"],
        "source": source,
        "item_code": program.item_code,
        "item_name": program.name,
        "short_name": short_name,
        "price": None,
        "price_label": program.price_label,
        "review_average": 0,
        "review_count": 0,
        "item_url": program.link,
        "affiliate_url": program.link,
        "impression": program.impression,
        "image_path": rel[0],
        "images": rel,
        "headline": headline,
        "points": points,
        "caption": caption,
        "status": "prepared",
        "created_at": storage.now_iso(),
    }


def _render_explainer(settings: config.Settings, number: int, genre: dict, program: a8.A8Program, ex) -> list[Path]:
    """表紙 → 悩み → 比較 → 手順 → 公式画像 → ポイント → こんな人に → まとめ（最大8枚）."""
    seq = iter(range(1, 11))
    out = lambda: _out(number, next(seq))  # noqa: E731
    slides = [render_explainer_cover(out_path=out(), number=number, genre=genre, hook=ex.cover_hook,
                                     sub=ex.cover_sub, account_name=settings.account_name)]
    if ex.pains:
        slides.append(render_list_slide(out_path=out(), number=number, genre=genre, title=ex.pains_title,
                                        items=ex.pains, mark="pain"))
    if ex.compare_rows:
        slides.append(render_compare_slide(out_path=out(), number=number, genre=genre, title=ex.compare_title,
                                           columns=ex.compare_columns, rows=ex.compare_rows))
    if ex.steps:
        slides.append(render_steps_slide(out_path=out(), number=number, genre=genre, title=ex.steps_title,
                                         steps=ex.steps))
    for url in program.images[:1]:
        path = render_official_image_slide(out_path=_out(number, len(slides) + 1), number=number, genre=genre,
                                           title=ex.short_name, image_url=url)
        if path is not None:
            slides.append(path)
            next(seq)
    slides.append(render_list_slide(out_path=out(), number=number, genre=genre, title=f"{ex.short_name}のポイント",
                                    items=ex.features, mark="check",
                                    footnote="※公式サイト・広告主の情報をもとにしています"))
    if ex.fit:
        slides.append(render_list_slide(out_path=out(), number=number, genre=genre, title="こんな人におすすめ",
                                        items=ex.fit, mark="check"))
    slides.append(render_cta_slide(out_path=out(), number=number, genre=genre, short_name=ex.short_name,
                                   price_label=program.price_label, account_name=settings.account_name))
    return slides


def _build_roundup(settings: config.Settings, writer: CaptionGenerator, number: int, genre: dict,
                   items: list[Item], theme: str, comment: str, source: str) -> dict:
    copy = writer.generate_roundup(items, genre, number, theme, comment)
    images = [render_roundup_cover(out_path=_out(number, 1), number=number, genre=genre, title=copy.title,
                                   subtitle=copy.subtitle, names=[c.short_name for c in copy.items],
                                   account_name=settings.account_name)]
    records = []
    for i, (item, c) in enumerate(zip(items, copy.items), start=1):
        path = render_roundup_item(
            out_path=_out(number, i + 1), number=number, genre=genre, index=i, total=len(items),
            short_name=c.short_name, best_for=c.best_for, points=c.points, price=item.price,
            review_average=item.review_average, review_count=item.review_count,
            product_image_url=item.image_url if settings.use_product_image else None,
        )
        images.append(path)
        records.append(_item_record(item, c.short_name, path) | {"best_for": c.best_for})
    images.append(render_roundup_summary(out_path=_out(number, len(images) + 1), number=number, genre=genre,
                                         rows=records))
    rel = [str(p.relative_to(config.ROOT)) for p in images]
    return {
        "number": number,
        "kind": "roundup",
        "genre": genre["id"],
        "source": source,
        "title": copy.title,
        "theme": theme,
        "items": records,
        "image_path": rel[0],
        "images": rel,
        "caption": compose_roundup_caption(copy, items, number),
        "status": "prepared",
        "created_at": storage.now_iso(),
    }


def _candidates(items: list[Item], exclude: set[str], settings: config.Settings) -> list[Item]:
    seen: set[str] = set()
    out = []
    for it in sorted(items, key=lambda it: it.review_count * it.review_average, reverse=True):
        if (it.item_code in exclude or it.item_code in seen
                or it.review_count < settings.min_review_count or it.review_average < settings.min_review_average):
            continue
        seen.add(it.item_code)
        out.append(it)
    return out


def find_item(rakuten: RakutenClient, genre: dict, exclude: set[str], settings: config.Settings,
              rng: random.Random) -> Item:
    keywords = genre["keywords"][:]
    rng.shuffle(keywords)
    for keyword in keywords[:3]:
        item = choose_item(rakuten.search(keyword), exclude, settings, rng)
        if item:
            return item
        time.sleep(1.1)  # 楽天APIのレート制限対策
    raise RuntimeError(f"{genre['label']} で条件に合う商品が見つかりませんでした")


def find_items(rakuten: RakutenClient, genre: dict, exclude: set[str], settings: config.Settings,
               rng: random.Random, count: int) -> tuple[list[Item], str]:
    """まとめ投稿用に、同じキーワード（＝同じ種類の商品）から複数件選ぶ.

    同じショップの色違い・サイズ違いばかりにならないよう、1ショップ1商品まで。
    """
    keywords = genre["keywords"][:]
    rng.shuffle(keywords)
    best: tuple[list[Item], str] = ([], "")
    for keyword in keywords[:3]:
        picked, shops = [], set()
        for it in _candidates(rakuten.search(keyword), exclude, settings):
            if it.shop_name in shops:
                continue
            shops.add(it.shop_name)
            picked.append(it)
            if len(picked) == count:
                break
        if len(picked) == count:
            return sorted(picked, key=lambda it: it.price), keyword
        if len(picked) > len(best[0]):
            best = (picked, keyword)
        time.sleep(1.1)
    if len(best[0]) >= 3:
        return sorted(best[0], key=lambda it: it.price), best[1]
    raise RuntimeError(f"{genre['label']} でまとめ投稿に使える商品が3件以上見つかりませんでした")


def public_image_urls(settings: config.Settings, post: dict) -> list[str]:
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL が設定されていません（例: https://<user>.github.io/sns-affiliate）")
    paths = post.get("images") or [post["image_path"]]
    return [f"{settings.public_base_url}/images/{Path(p).name}" for p in paths]


def wait_until_reachable(url: str, timeout_sec: int = 600, interval: int = 15, content_prefix: str = "image/") -> None:
    """GitHub Pages の反映を待つ（Instagram は公開URLから画像を取得するため）."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.head(url, timeout=15, allow_redirects=True)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith(content_prefix):
                return
        except requests.RequestException:
            pass
        time.sleep(interval)
    raise RuntimeError(f"画像URLにアクセスできません: {url}")


def publish(settings: config.Settings, *, ig: InstagramClient | None = None,
            wait: bool = True, limit: int = 1) -> list[dict]:
    """prepared の投稿を古い順に Instagram へ公開する."""
    posts = storage.load_posts()
    targets = [p for p in posts if p["status"] == "prepared"][:limit]
    if not targets:
        return []
    ig = ig or InstagramClient(settings)
    done = []
    for post in targets:
        urls = public_image_urls(settings, post)
        try:
            if wait:
                for url in urls:
                    wait_until_reachable(url)
            media = ig.publish(urls, post["caption"])
        except Exception as e:  # 失敗は記録して次回以降に持ち越さない
            post["status"] = "failed"
            post["error"] = str(e)[:500]
        else:
            post.update(status="published", media_id=media["id"], permalink=media["permalink"],
                        published_at=storage.now_iso())
        done.append(post)
        storage.save_posts(posts)
    build_site(posts, config.load_genres(), settings.site_title)
    return done


def publish_reels(settings: config.Settings, *, ig: InstagramClient | None = None,
                  wait: bool = True, limit: int = 1) -> list[dict]:
    """カルーセルを投稿済みで、リールが投稿待ちのものを古い順に投稿する。古い動画ファイルは削除する."""
    posts = storage.load_posts()
    targets = [p for p in posts if p.get("status") == "published" and p.get("reel_status") == "pending"][:limit]
    done = []
    if targets:
        ig = ig or InstagramClient(settings)
    for post in targets:
        if not settings.public_base_url:
            raise RuntimeError("PUBLIC_BASE_URL が設定されていません")
        video_url = f"{settings.public_base_url}/videos/{Path(post['reel_path']).name}"
        try:
            if wait:
                wait_until_reachable(video_url, content_prefix="video/")
            media = ig.publish_reel(video_url, post["caption"])
        except Exception as e:  # noqa: BLE001
            post["reel_status"] = "failed"
            post["reel_error"] = str(e)[:500]
        else:
            post.update(reel_status="published", reel_media_id=media["id"], reel_permalink=media["permalink"],
                        reel_published_at=storage.now_iso())
        done.append(post)
        storage.save_posts(posts)
    prune_videos(posts, settings.reel_keep_days)
    storage.save_posts(posts)
    return done


def prune_videos(posts: list[dict], keep_days: int) -> int:
    """投稿済み（または失敗）から keep_days 日を過ぎた動画を消す（GitHub Pages の容量 1GB 対策）."""
    now = datetime.now(storage.JST)
    removed = 0
    for p in posts:
        path = p.get("reel_path")
        if not path or p.get("reel_status") == "pending":
            continue
        stamp = p.get("reel_published_at") or p.get("created_at")
        if now - datetime.fromisoformat(stamp) < timedelta(days=keep_days):
            continue
        f = config.ROOT / path
        if f.exists():
            f.unlink()
            removed += 1
        p["reel_path"] = None
    return removed


def collect_insights(settings: config.Settings, *, ig: InstagramClient | None = None,
                     max_age_days: int = 30) -> int:
    """公開済み投稿（24時間〜30日以内）のインサイトを取得して CSV に出力する."""
    posts = storage.load_posts()
    ig = ig or InstagramClient(settings)
    now = datetime.now(storage.JST)
    updated = 0
    for post in posts:
        if post.get("status") != "published" or not post.get("media_id"):
            continue
        age = now - datetime.fromisoformat(post["published_at"])
        if age < timedelta(hours=24) or age > timedelta(days=max_age_days):
            continue
        try:
            post["insights"] = ig.insights(post["media_id"])
            post["insights_at"] = storage.now_iso()
            updated += 1
        except Exception as e:
            post["insights_error"] = str(e)[:300]
    storage.save_posts(posts)
    write_insights_csv(posts)
    return updated


def write_insights_csv(posts: list[dict], path: Path = config.INSIGHTS_CSV) -> None:
    metrics = sorted({k for p in posts for k in (p.get("insights") or {})})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["number", "kind", "genre", "title", "price", "published_at", "permalink", *metrics])
        for p in posts:
            if p.get("status") != "published":
                continue
            ins = p.get("insights") or {}
            title = p.get("title") or p.get("short_name", "")
            price = "/".join(str(it.get("price") or it.get("price_label") or "") for it in storage.post_items(p))
            w.writerow([p["number"], p.get("kind", "single"), p["genre"], title, price,
                        p.get("published_at", ""), p.get("permalink", ""),
                        *[ins.get(m, "") for m in metrics]])


# ---------------------------------------------------------------------------
# ③ 下書きをまとめて作る / ④ 伸びた投稿の作り直し / 承認された下書きを投稿する
# ---------------------------------------------------------------------------

TIPS_TOPICS = {
    "training": ["パワーグリップとリストラップの違いと選び方", "宅トレを続けるための道具のそろえ方",
                 "ジム初心者が最初に用意すると便利なもの", "トレーニングベルトを使うタイミング",
                 "ホームジムを作るときの床と騒音の対策"],
    "supplement": ["プロテインの種類（ホエイ・ソイ・カゼイン）の違い", "プロテインの選び方：味・溶けやすさ・コスパ",
                   "成分表示の見方（たんぱく質量・カロリー）", "シェイカーの選び方と洗いやすさ",
                   "食事でたんぱく質をとるコツと、プロテインの使いどころ"],
}


def _subjects(settings: config.Settings, genres: list[dict], rakuten: RakutenClient | None,
              rng: random.Random) -> list[dict]:
    """下書きの題材の候補（A8 案件・楽天の商品・お役立ちテーマ）."""
    enabled = {g["id"] for g in genres}
    subjects: list[dict] = []
    for pr in a8.usable(a8.load_programs()):
        if pr.genre in enabled:
            subjects.append({"kind": "a8", "genre": pr.genre, "subject": pr.id, "name": pr.name, "facts": pr.facts,
                             "price_label": pr.price_label, "notes": pr.notes, "ng_words": pr.ng_words,
                             "link": pr.link, "impression": pr.impression, "images": pr.images[:1]})
    if rakuten is not None:
        for g in genres:
            kw = rng.choice(g["keywords"])
            for it in _candidates(rakuten.search(kw), set(), settings)[:2]:
                subjects.append({"kind": "rakuten", "genre": g["id"], "subject": it.item_code,
                                 "name": it.name[:40], "facts": [f"価格 {it.price:,}円（税込・作成時点）",
                                                                 f"レビュー平均{it.review_average:.2f}（{it.review_count:,}件）",
                                                                 it.caption[:300]],
                                 "price_label": f"¥{it.price:,}", "notes": "価格は作成時点。", "ng_words": [],
                                 "link": it.affiliate_url, "impression": "", "images": []})
            time.sleep(1.1)
    return subjects


def generate_drafts(settings: config.Settings, *, count: int | None = None, writer: CaptionGenerator | None = None,
                    rakuten: RakutenClient | None = None, rng: random.Random | None = None,
                    render_previews: bool = True) -> list:
    """③ ①でまとめた型を使って、下書きをまとめて作る（商品紹介:お役立ち = だいたい 7:3）."""
    from . import drafts as dr
    from .research import load_patterns

    rng = rng or random.Random()
    count = count or settings.drafts_per_batch
    writer = writer or CaptionGenerator(settings.claude_model)
    genres = config.load_genres()
    if rakuten is None and config.rakuten_ready(settings):
        rakuten = RakutenClient(settings)
    subjects = _subjects(settings, genres, rakuten, rng)
    patterns = load_patterns()
    used: dict[str, int] = {}
    made = []
    next_id = dr.next_id()
    for i in range(count):
        genre = genres[i % len(genres)]
        pattern = patterns[i % len(patterns)]
        audience = rng.choice(genre.get("audiences") or ["このジャンルに興味がある人"])
        pool = [s for s in subjects if s["genre"] == genre["id"] and used.get(s["subject"], 0) < 2]
        if pool and (i % 10) not in (3, 6, 9):  # 10本のうち3本はお役立ち投稿
            subj = rng.choice(pool)
        else:
            topic = rng.choice(TIPS_TOPICS.get(genre["id"]) or [f"{genre['label']}の選び方"])
            subj = {"kind": "tips", "genre": genre["id"], "subject": topic, "name": topic, "facts": [],
                    "price_label": "", "notes": "", "ng_words": [], "link": "", "impression": "", "images": []}
        used[subj["subject"]] = used.get(subj["subject"], 0) + 1
        copy = writer.generate_draft(subj, genre, pattern, audience)
        d = dr.Draft(id=next_id + i, kind=subj["kind"], genre_id=genre["id"], subject=subj["subject"], name=subj["name"],
                     copy=copy, link=subj["link"], impression=subj["impression"], price_label=subj["price_label"],
                     images=subj["images"], pattern=pattern["name"], audience=audience)
        dr.save(d)
        if render_previews:
            dr.render_preview(d, settings.account_name)
        made.append(d)
    if render_previews:
        dr.build_page(settings.repo_slug)
    return made


def remix_hits(settings: config.Settings, *, limit: int = 2, min_age_days: int = 7,
               writer: CaptionGenerator | None = None, render_previews: bool = True) -> list:
    """④ 公開から1週間以上たった投稿のうち、保存数が多いものを、角度を変えた下書きにする（1投稿につき1回）."""
    from . import drafts as dr
    from .research import load_patterns

    posts = storage.load_posts()
    now = datetime.now(storage.JST)
    hits = [p for p in posts
            if p.get("status") == "published" and not p.get("remixed") and p.get("draft_id")
            and p.get("published_at") and now - datetime.fromisoformat(p["published_at"]) >= timedelta(days=min_age_days)
            and (p.get("insights") or {}).get("saved", 0) > 0]
    hits.sort(key=lambda p: (p["insights"].get("saved", 0), p["insights"].get("shares", 0)), reverse=True)
    if not hits:
        return []
    writer = writer or CaptionGenerator(settings.claude_model)
    by_id = {d.id: d for d in dr.list_drafts(include_done=True)}
    patterns = load_patterns()
    made = []
    for i, post in enumerate(hits[:limit]):
        src = by_id.get(post["draft_id"])
        if src is None:
            continue
        genre = dr.genre_for(src)
        audiences = [a for a in genre.get("audiences", []) if a != src.audience] or [src.audience]
        pattern = next((p for p in patterns if p["name"] != src.pattern), patterns[0])
        subject = {"kind": src.kind, "name": src.name, "subject": src.subject, "facts": [], "price_label": src.price_label,
                   "notes": "", "ng_words": []}
        if src.kind == "a8":
            prog = next((pr for pr in a8.load_programs() if pr.id == src.subject), None)
            if prog:
                subject.update(facts=prog.facts, notes=prog.notes, ng_words=prog.ng_words)
        elif src.kind == "rakuten":
            subject["facts"] = src.copy.features
        copy = writer.generate_draft(subject, genre, pattern, audiences[i % len(audiences)],
                                     remix_of={"headline": post.get("headline", ""), "caption": post.get("caption", "")})
        d = dr.Draft(id=dr.next_id(), kind=src.kind, genre_id=src.genre_id, subject=src.subject, name=src.name,
                     copy=copy, link=src.link, impression=src.impression, price_label=src.price_label,
                     images=src.images, pattern=pattern["name"], audience=audiences[i % len(audiences)],
                     remix_of=post["number"])
        dr.save(d)
        if render_previews:
            dr.render_preview(d, settings.account_name)
        post["remixed"] = True
        made.append(d)
    storage.save_posts(posts)
    if render_previews and made:
        dr.build_page(settings.repo_slug)
    return made


def publish_draft(settings: config.Settings) -> dict:
    """承認済みの下書きを1本、投稿データにする（このあと publish で Instagram に公開）."""
    from . import drafts as dr
    from .caption import PR_LINE, _with_pr

    d = dr.next_approved()
    if d is None:
        raise NothingToPost("承認済みの下書きがないので、今日は投稿しません（確認ページで「承認: はい」にしてください）")
    posts = storage.load_posts()
    number = storage.next_number(posts)
    genre = dr.genre_for(d)
    images = dr.render(d, config.IMAGES_DIR, number, settings.account_name, prefix=f"{number:04d}-")
    rel = [str(p.relative_to(config.ROOT)) for p in images]
    tags = d.copy.hashtags
    if d.kind == "tips":
        caption = f"{d.copy.caption}\n\n" + " ".join(tags[:30])
    else:
        lead = PR_LINE if d.kind == "rakuten" else "【PR】A8.netのアフィリエイト広告を利用しています"
        price = f"💰 {d.price_label}（投稿時点）\n" if d.price_label else ""
        caption = (f"{lead}\n\n{d.copy.caption}\n\n🛒 詳しくはプロフィールのリンク → #{number:03d}\n{price}"
                   "※料金・条件は変わることがあります。最新の内容は販売ページ・公式サイトでご確認ください\n\n" + _with_pr(tags))
    post = {
        "number": number,
        "kind": "single",
        "format": "explainer",
        "network": {"a8": "a8", "rakuten": "rakuten"}.get(d.kind, "none"),
        "genre": d.genre_id,
        "source": "draft",
        "draft_id": d.id,
        "item_code": f"a8:{d.subject}" if d.kind == "a8" else d.subject,
        "item_name": d.name,
        "short_name": d.copy.short_name,
        "price": None,
        "price_label": d.price_label,
        "review_average": 0,
        "review_count": 0,
        "item_url": d.link,
        "affiliate_url": d.link,
        "impression": d.impression,
        "image_path": rel[0],
        "images": rel,
        "headline": d.copy.cover_hook,
        "points": d.copy.features,
        "caption": caption[:2200],
        "status": "prepared",
        "created_at": storage.now_iso(),
    }
    if settings.make_reels and len(rel) >= 3:
        _attach_reel(settings, post, genre)
    posts.append(post)
    storage.save_posts(posts)
    dr.mark_published(d, number)
    build_site(posts, config.load_genres(), settings.site_title)
    dr.build_page(settings.repo_slug)
    return post
