"""商品選定 → 投稿文生成 → 画像生成 → 公開 → 計測 のパイプライン."""

from __future__ import annotations

import csv
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import config, inbox, storage
from .caption import CaptionGenerator, compose_caption
from .image_card import render_card, render_photo_cover, render_photo_slide
from .instagram import InstagramClient
from .rakuten import Item, RakutenClient, item_code_from_url
from .site import build_site


def pick_genre(genres: list[dict], number: int, genre_id: str | None = None) -> dict:
    if genre_id:
        for g in genres:
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


def prepare(settings: config.Settings, *, genre_id: str | None = None, use_inbox: bool = True,
            rakuten: RakutenClient | None = None, writer: CaptionGenerator | None = None,
            rng: random.Random | None = None, inbox_dir: Path | None = None) -> dict:
    """次の投稿を1件準備し、posts.json と一覧ページを更新する.

    inbox/ に自分で撮った写真があればそれを優先してカルーセル投稿を作る。
    なければ楽天から商品を自動で選び、文字カード1枚の投稿を作る。
    """
    rng = rng or random.Random()
    genres = config.load_genres()
    posts = storage.load_posts()
    number = storage.next_number(posts)
    rakuten = rakuten or RakutenClient(settings)
    writer = writer or CaptionGenerator(settings.claude_model)

    entries = inbox.list_entries(inbox_dir) if use_inbox else []
    entry = entries[0] if entries else None
    if entry:
        item = rakuten.lookup(item_code_from_url(entry.url))
        if item is None:
            raise RuntimeError(f"inbox/{entry.name}: 楽天で商品が見つかりません（URLを確認してください）: {entry.url}")
        genre = pick_genre(genres, number, genre_id or entry.genre_id)
        comment, photos = entry.comment, entry.photos
    else:
        genre = pick_genre(genres, number, genre_id)
        item = find_item(rakuten, genre, storage.recently_posted_codes(posts, settings.repost_cooldown_days),
                         settings, rng)
        comment, photos = "", []

    copy = writer.generate(item, genre, number, comment, photos)

    card_kwargs = dict(number=number, genre=genre, headline=copy.headline, short_name=copy.short_name,
                       points=copy.points, price=item.price, review_average=item.review_average,
                       review_count=item.review_count, account_name=settings.account_name)
    images: list[Path] = []
    if photos:
        out = lambda i: config.IMAGES_DIR / f"{number:04d}-{i:02d}.jpg"  # noqa: E731
        images.append(render_photo_cover(photo=photos[0], out_path=out(1), number=number, genre=genre,
                                         headline=copy.headline, short_name=copy.short_name))
        for i, (photo, telop) in enumerate(zip(photos[1:], copy.slide_captions), start=2):
            images.append(render_photo_slide(photo=photo, out_path=out(i), caption=telop))
        images.append(render_card(out_path=out(len(images) + 1), **card_kwargs))
    else:
        images.append(render_card(
            out_path=config.IMAGES_DIR / f"{number:04d}.jpg",
            product_image_url=item.image_url if settings.use_product_image else None,
            **card_kwargs,
        ))

    rel = [str(p.relative_to(config.ROOT)) for p in images]
    post = {
        "number": number,
        "genre": genre["id"],
        "source": "photo" if photos else "auto",
        "item_code": item.item_code,
        "item_name": item.name,
        "short_name": copy.short_name,
        "price": item.price,
        "review_average": item.review_average,
        "review_count": item.review_count,
        "item_url": item.item_url,
        "affiliate_url": item.affiliate_url,
        "image_path": rel[0],
        "images": rel,
        "headline": copy.headline,
        "points": copy.points,
        "caption": compose_caption(copy, item),
        "status": "prepared",
        "created_at": storage.now_iso(),
    }
    posts.append(post)
    storage.save_posts(posts)
    if entry:
        inbox.mark_used(entry, number)
    build_site(posts, genres, settings.site_title)
    return post


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


def public_image_urls(settings: config.Settings, post: dict) -> list[str]:
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL が設定されていません（例: https://<user>.github.io/sns-affiliate）")
    paths = post.get("images") or [post["image_path"]]
    return [f"{settings.public_base_url}/images/{Path(p).name}" for p in paths]


def wait_until_reachable(url: str, timeout_sec: int = 600, interval: int = 15) -> None:
    """GitHub Pages の反映を待つ（Instagram は公開URLから画像を取得するため）."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.head(url, timeout=15, allow_redirects=True)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
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
        w.writerow(["number", "genre", "short_name", "price", "published_at", "permalink", *metrics])
        for p in posts:
            if p.get("status") != "published":
                continue
            ins = p.get("insights") or {}
            w.writerow([p["number"], p["genre"], p.get("short_name", ""), p["price"],
                        p.get("published_at", ""), p.get("permalink", ""),
                        *[ins.get(m, "") for m in metrics]])
