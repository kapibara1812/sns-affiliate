"""CLI: python -m sns_affiliate <command>"""

from __future__ import annotations

import argparse
import random
import sys

from . import config, pipeline, storage
from .caption import PostCopy, compose_caption
from .image_card import render_card
from .instagram import InstagramClient
from .rakuten import Item
from .site import build_site


def cmd_prepare(args, settings):
    post = pipeline.prepare(settings, genre_id=args.genre, use_inbox=not args.no_inbox)
    kind = "写真カルーセル" if post.get("source") == "photo" else "自動選定"
    print(f"#{post['number']:03d} [{post['genre']}] {post['short_name']}（{kind}・{len(post['images'])}枚）を準備しました")
    print("-" * 40)
    print(post["caption"])


def cmd_publish(args, settings):
    results = pipeline.publish(settings, wait=not args.no_wait, limit=args.limit)
    if not results:
        print("公開待ちの投稿はありません")
    failed = False
    for p in results:
        if p["status"] == "published":
            print(f"#{p['number']:03d} を公開しました: {p['permalink']}")
        else:
            failed = True
            print(f"#{p['number']:03d} の公開に失敗: {p.get('error')}", file=sys.stderr)
    return 1 if failed else 0


def cmd_insights(args, settings):
    n = pipeline.collect_insights(settings)
    print(f"{n} 件のインサイトを更新しました → {config.INSIGHTS_CSV.relative_to(config.ROOT)}")


def cmd_site(args, settings):
    out = build_site(storage.load_posts(), config.load_genres(), settings.site_title)
    print(f"一覧ページを更新しました → {out.relative_to(config.ROOT)}")


def cmd_refresh_token(args, settings):
    data = InstagramClient(settings).refresh_token()
    days = int(data.get("expires_in", 0)) // 86400
    print(f"新しいトークン（有効期限 約{days}日）を GitHub Secrets の IG_ACCESS_TOKEN に設定してください:")
    print(data["access_token"])


def cmd_demo(args, settings):
    """API を使わずにサンプル画像とキャプションを出力する（デザイン確認用）."""
    genres = config.load_genres()
    genre = pipeline.pick_genre(genres, 1, args.genre or "gadget")
    item = Item(item_code="demo:1", name="サンプル ワイヤレスイヤホン ノイズキャンセリング", price=7980,
                item_url="https://item.rakuten.co.jp/", affiliate_url="https://hb.afl.rakuten.co.jp/",
                image_url="", review_average=4.52, review_count=3210, shop_name="サンプルショップ", caption="")
    copy = PostCopy(headline="1万円以下で静寂が手に入る", points=["強力ノイキャン搭載", "最大30時間再生", "レビュー3,000件超え"],
                    short_name="サンプル ノイキャンイヤホン", caption="（ここに Claude が生成した本文が入ります）",
                    hashtags=genre["hashtags"])
    out = render_card(out_path=config.ROOT / "data" / "demo.jpg", number=random.randint(1, 999), genre=genre,
                      headline=copy.headline, short_name=copy.short_name, points=copy.points,
                      price=item.price, review_average=item.review_average, review_count=item.review_count,
                      account_name=settings.account_name)
    print(f"サンプル画像 → {out.relative_to(config.ROOT)}\n")
    print(compose_caption(copy, item))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sns_affiliate", description="Instagram × 楽天アフィリエイト 自動投稿")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="商品を選び、投稿文と画像を生成する")
    p.add_argument("--genre", help="ジャンルID（未指定ならローテーション）")
    p.add_argument("--no-inbox", action="store_true", help="inbox/ の写真を使わず自動選定する")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("publish", help="準備済みの投稿を Instagram に公開する")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--no-wait", action="store_true", help="画像URLの公開待ちをしない")
    p.set_defaults(func=cmd_publish)

    sub.add_parser("insights", help="投稿のインサイトを取得して CSV 出力").set_defaults(func=cmd_insights)
    sub.add_parser("site", help="プロフィールリンク用ページを再生成").set_defaults(func=cmd_site)
    sub.add_parser("refresh-token", help="Instagram 長期トークンを更新").set_defaults(func=cmd_refresh_token)

    p = sub.add_parser("demo", help="APIなしでサンプル画像を生成")
    p.add_argument("--genre")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args, config.Settings()) or 0


if __name__ == "__main__":
    sys.exit(main())
