"""商品選定 → 投稿文生成 → 画像生成 → 公開 → 計測 のパイプライン."""

from __future__ import annotations

import csv
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import a8, config, inbox, storage
from .caption import CaptionGenerator, compose_a8_caption, compose_caption, compose_roundup_caption
from .image_card import (render_card, render_photo_cover, render_photo_slide, render_roundup_cover,
                         render_roundup_item, render_roundup_summary)
from .instagram import InstagramClient
from .rakuten import Item, RakutenClient, item_code_from_url
from .site import build_site


class NothingToPost(RuntimeError):
    """今日は投稿できるネタがない（エラーではなく、静かに休む）."""


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
        program = a8.pick_program(programs, posts, settings.a8_cooldown_days, genre_id)
        if program is None and a8_only:
            if not a8.usable(programs):
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

    posts.append(post)
    storage.save_posts(posts)
    if entry:
        inbox.mark_used(entry, number)
    build_site(posts, genres, settings.site_title)
    return post


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
            images.append(render_photo_slide(photo=photo, out_path=_out(number, i), caption=telop))
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
    copy = writer.generate_a8(program, genre, number, comment, photos)
    card_kwargs = dict(number=number, genre=genre, headline=copy.headline, short_name=copy.short_name,
                       points=copy.points, price=0, review_average=0, review_count=0,
                       account_name=settings.account_name,
                       price_label=program.price_label or "公式サイトをチェック",
                       cta_verb="詳細は", footnote="※条件・料金は公式サイトでご確認ください")
    images: list[Path] = []
    if photos:
        images.append(render_photo_cover(photo=photos[0], out_path=_out(number, 1), number=number, genre=genre,
                                         headline=copy.headline, short_name=copy.short_name))
        for i, (photo, telop) in enumerate(zip(photos[1:], copy.slide_captions), start=2):
            images.append(render_photo_slide(photo=photo, out_path=_out(number, i), caption=telop))
        images.append(render_card(out_path=_out(number, len(images) + 1), **card_kwargs))
    else:
        images.append(render_card(out_path=_out(number), **card_kwargs))
    rel = [str(p.relative_to(config.ROOT)) for p in images]
    return {
        "number": number,
        "kind": "single",
        "network": "a8",
        "genre": genre["id"],
        "source": source,
        "item_code": program.item_code,
        "item_name": program.name,
        "short_name": copy.short_name,
        "price": None,
        "price_label": program.price_label,
        "review_average": 0,
        "review_count": 0,
        "item_url": program.link,
        "affiliate_url": program.link,
        "impression": program.impression,
        "image_path": rel[0],
        "images": rel,
        "headline": copy.headline,
        "points": copy.points,
        "caption": compose_a8_caption(copy, program),
        "status": "prepared",
        "created_at": storage.now_iso(),
    }


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
