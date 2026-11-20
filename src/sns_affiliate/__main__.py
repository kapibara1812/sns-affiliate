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
    try:
        post = pipeline.prepare(settings, genre_id=args.genre, use_inbox=not args.no_inbox, mode=args.mode)
    except pipeline.NothingToPost as e:
        print(f"お休み: {e}")
        return 0
    kind = {"photo": "写真カルーセル", "pick": "指定商品", "auto": "自動選定"}[post["source"]]
    if post.get("network") == "a8":
        kind = "A8.net・" + kind
    title = post.get("title") or post.get("short_name")
    form = "まとめ" if post["kind"] == "roundup" else "単品"
    print(f"#{post['number']:03d} [{post['genre']}] {title}（{kind}・{form}・{len(post['images'])}枚）を準備しました")
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


def cmd_publish_reel(args, settings):
    results = pipeline.publish_reels(settings, wait=not args.no_wait, limit=args.limit)
    if not results:
        print("投稿待ちのリールはありません")
    failed = False
    for p in results:
        if p.get("reel_status") == "published":
            print(f"#{p['number']:03d} のリールを公開しました: {p.get('reel_permalink')}")
        else:
            failed = True
            print(f"#{p['number']:03d} のリールの公開に失敗: {p.get('reel_error')}", file=sys.stderr)
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
    if args.roundup:
        return _demo_roundup(pipeline.pick_genre(genres, 1, args.genre or "travel"), settings)
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


def _demo_roundup(genre, settings):
    from .caption import RoundupCopy, RoundupItemCopy, compose_roundup_caption
    from .image_card import render_roundup_cover, render_roundup_item, render_roundup_summary

    samples = [("サンプル 圧縮ポーチ3点セット", 1980, 4.6, 5120, "荷物を減らしたい人に", ["衣類を約半分に圧縮", "3サイズで仕分けできる"]),
               ("サンプル 吊り下げ洗面ポーチ", 2480, 4.5, 2310, "ホテルの洗面台が狭い人に", ["フックで吊り下げ可能", "防水素材で濡れても安心"]),
               ("サンプル ガジェットポーチ", 2980, 4.4, 1870, "充電器が多い人に", ["ケーブル類を一括収納", "自立するので取り出しやすい"]),
               ("サンプル 機内用ネックピロー", 3480, 4.3, 980, "長距離フライトの人に", ["低反発で首を支える", "コンパクトに収納できる"])]
    items = [Item(item_code=f"demo:{i}", name=n, price=pr, item_url="", affiliate_url="", image_url="",
                  review_average=a, review_count=c, shop_name="", caption="") for i, (n, pr, a, c, _, _) in enumerate(samples)]
    copy = RoundupCopy(title="旅の荷物がスッと収まる収納グッズ4選", subtitle="レビュー評価4.3以上だけを厳選",
                       items=[RoundupItemCopy(n, b, pts) for n, _, _, _, b, pts in samples],
                       caption="（ここに Claude が生成した導入文が入ります）", hashtags=genre["hashtags"])
    out = config.ROOT / "data" / "demo"
    number = 12
    render_roundup_cover(out_path=out / "roundup-1.jpg", number=number, genre=genre, title=copy.title,
                         subtitle=copy.subtitle, names=[c.short_name for c in copy.items],
                         account_name=settings.account_name)
    rows = []
    for i, (it, c) in enumerate(zip(items, copy.items), 1):
        render_roundup_item(out_path=out / f"roundup-{i + 1}.jpg", number=number, genre=genre, index=i,
                            total=len(items), short_name=c.short_name, best_for=c.best_for, points=c.points,
                            price=it.price, review_average=it.review_average, review_count=it.review_count)
        rows.append({"short_name": c.short_name, "best_for": c.best_for, "price": it.price,
                     "review_average": it.review_average, "review_count": it.review_count})
    render_roundup_summary(out_path=out / f"roundup-{len(items) + 2}.jpg", number=number, genre=genre, rows=rows)
    print(f"サンプル画像 → {out.relative_to(config.ROOT)}/roundup-*.jpg\n")
    print(compose_roundup_caption(copy, items, number))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sns_affiliate", description="Instagram × 楽天アフィリエイト 自動投稿")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="商品を選び、投稿文と画像を生成する")
    p.add_argument("--genre", help="ジャンルID（未指定ならローテーション）")
    p.add_argument("--no-inbox", action="store_true", help="inbox/ を使わず自動選定する")
    p.add_argument("--mode", choices=["auto", "single", "roundup", "a8"], default="auto",
                   help="inbox が空のときの投稿形式（auto: A8曜日はA8案件・まとめ曜日はまとめ・それ以外は楽天の単品）")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("publish", help="準備済みの投稿を Instagram に公開する")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--no-wait", action="store_true", help="画像URLの公開待ちをしない")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("publish-reel", help="カルーセル投稿済みのリール動画を公開する（翌日用）")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--no-wait", action="store_true", help="動画URLの公開待ちをしない")
    p.set_defaults(func=cmd_publish_reel)

    sub.add_parser("insights", help="投稿のインサイトを取得して CSV 出力").set_defaults(func=cmd_insights)
    sub.add_parser("site", help="プロフィールリンク用ページを再生成").set_defaults(func=cmd_site)
    sub.add_parser("refresh-token", help="Instagram 長期トークンを更新").set_defaults(func=cmd_refresh_token)

    p = sub.add_parser("demo", help="APIなしでサンプル画像を生成")
    p.add_argument("--genre")
    p.add_argument("--roundup", action="store_true", help="まとめ投稿のサンプルを作る")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args, config.Settings()) or 0


if __name__ == "__main__":
    sys.exit(main())
