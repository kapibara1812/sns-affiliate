# sns-affiliate

> 🔰 **はじめての方は [GUIDE.md（はじめてガイド）](GUIDE.md) から読んでください。** 登録から運用までを順番に説明しています。

Instagram × 楽天アフィリエイトの**自動投稿パイプライン**です。
毎日、楽天市場から商品を選び、Claude で投稿文を書き、投稿画像を作って Instagram に公開します。
購入ページへのリンクは、プロフィールに置く一覧ページから開いてもらいます。

```
┌───────────────┐   ┌────────────────┐   ┌───────────────┐   ┌─────────────────┐
│ 楽天商品検索API │ → │ Claude          │ → │ 画像カード生成  │ → │ GitHub Pages     │
│ レビュー数/評価 │   │ 見出し・推しポイ │   │ 1080x1350 JPEG │   │ 画像＋リンク一覧  │
│ で商品を選定    │   │ ント・キャプション│   │ PR表記・番号入り │   │（プロフィールURL）│
└───────────────┘   └────────────────┘   └───────────────┘   └────────┬────────┘
                                                                       ↓
                       ┌──────────────────┐                  ┌─────────────────┐
                       │ インサイト取得     │ ← 毎朝 ─────────  │ Instagram API    │
                       │ data/insights.csv │                  │ で自動投稿（毎晩）│
                       └──────────────────┘                  └─────────────────┘
```

**収益が発生する流れ**: 投稿を見る → プロフィールのリンクを開く → 投稿番号（例 `#012`）の商品をタップ → 楽天で購入 → 楽天アフィリエイト報酬

Instagram のキャプションに貼ったリンクはタップできません。そのため投稿画像とキャプションに番号を入れ、プロフィールの一覧ページ（`docs/index.html`）から購入ページへ移動してもらいます。

## 投稿の種類

毎日1件、次の優先順位で投稿を作ります。

| 優先 | きっかけ | 投稿の形 | 文章 |
|---|---|---|---|
| 1 | `inbox/` に**URL1つ＋写真** | 写真カルーセル（写真の表紙 → 使用写真＋テロップ → ポイントまとめ） | 本人の感想を元にした体験談。Claude は写真も見てテロップを付ける |
| 1 | `inbox/` に**URL1つだけ** | 文字カード1枚 | 事実（価格・レビュー件数）だけで紹介する |
| 1 | `inbox/` に**URL2〜8つ** | まとめ・比較カルーセル（表紙 → 商品ごとのスライド → 比較表） | 事実だけ。どんな人に向いているかで書き分ける |
| 1 | `inbox/` に**「A8: 案件ID」** | A8.net 案件の紹介（写真があれば写真カルーセル） | 本人の感想があれば体験談、なければ登録した特徴だけ |
| 2 | `inbox/` が空・**A8曜日**（初期設定は月・木）で使える案件がある | A8.net 案件の文字カード1枚（最後に投稿してから一番間が空いた案件） | 登録した特徴だけ |
| 2 | `inbox/` が空・**まとめ曜日**（初期設定は水・土） | まとめ・比較カルーセル（人気商品5件・1ショップ1件・価格の安い順） | 同上 |
| 3 | `inbox/` が空・それ以外の日 | 文字カード1枚（人気商品を自動で選ぶ） | 同上 |

まとめ投稿では、一覧ページに商品ごとの番号（`#012-1`, `#012-2` …）が付きます。
置き方は [inbox/README.txt](inbox/README.txt) を参照してください。まとめ投稿の曜日と件数は、Variables の `ROUNDUP_WEEKDAYS`（例 `3,6`。1=月 … 7=日）と `ROUNDUP_SIZE`（既定 5）で変えられます。

## 図解カルーセルとリール動画

写真がない A8.net 案件の投稿は、保存されやすい**図解カルーセル**（最大8枚）になります。

| 枚目 | 内容 |
|---|---|
| 1 | 表紙（読者の「あるある」や疑問） |
| 2 | 悩み（一般的な困りごと） |
| 3 | 比較表（一般的な選択肢を ◎○△× で比較。紹介するサービスの種類を強調） |
| 4 | 手順（一般的な使い方・選び方。不要なら省略） |
| 5 | 公式画像素材（`images` を登録した場合。切り抜き・加工せず縮小のみ） |
| 6 | サービスのポイント（登録した `facts` だけ） |
| 7 | こんな人におすすめ |
| 8 | まとめ（料金・プロフィールのリンク → 番号） |

3枚以上の投稿からは、同じ内容の**リール動画**（1080x1920・約25秒・H.264/AAC）も自動で作ります。

- カルーセルは毎日 20:07、リールは**翌日 12:07**（`reel-post.yml`）に投稿します。同じ日に同じ内容が2回並ばないようにするためです
- BGM を付けたい場合は、著作権フリーの音源を `assets/bgm.mp3` に置きます（なければ無音）
- 動画は投稿から `REEL_KEEP_DAYS`（既定 7）日後に `docs/videos/` から削除します（GitHub Pages の容量 1GB 対策）
- リールを作らない場合は、Variables に `MAKE_REELS` = `false` を登録します

## A8.net 対応

A8.net には商品検索の公開APIがないため、提携した案件を `config/a8_programs.json` に登録して使います（書き方は `config/a8_programs.example.json` と [GUIDE.md の STEP 10](GUIDE.md)）。

- `sns_allowed: true` の案件だけを使います。案件ごとに Instagram 掲載の可否を確認してから true にしてください
- `ng_words`（広告主の禁止表現）が投稿文に入っていたら、1回だけ自動で作り直します。それでも入っていたら止めます
- 一覧ページは「公式サイトを見る」ボタンになり、`impression` を登録すると A8 のインプレッション計測用画像も読み込みます
- 曜日と再投稿の間隔は Variables の `A8_WEEKDAYS`（既定 `1,4`）と `A8_COOLDOWN_DAYS`（既定 14）で変えられます
- **楽天のキー（3つ）が未登録の間は、曜日に関係なく毎日 A8.net の案件を使います**。使える案件がない日（未登録・全案件がクールダウン中）はエラーにせず、その日は投稿を休みます

## ジャンル

`config/genres.json` に定義された次の6ジャンルを、1日1件ずつ順番に投稿します。

| ID | ジャンル | 検索キーワード例 |
|---|---|---|
| gadget | ガジェット | ワイヤレスイヤホン、モバイルバッテリー |
| travel | 旅行 | 機内持ち込みスーツケース、圧縮バッグ |
| training | トレーニング | 可変式ダンベル、ヨガマット |
| appliance | 家電 | ロボット掃除機、電気ケトル |
| book | 本 | ビジネス書、本屋大賞 |
| investment | 投資 | 新NISA本、家計管理本（銘柄の推奨はしない） |

キーワード、ハッシュタグ、カードの色は JSON を編集するだけで変えられます。


### ジャンルの切り替えとデザイン
- `config/genres.json` の各ジャンルの `enabled` を `true` / `false` にすると、ローテーションに入れるかを切り替えられます（初期設定は ガジェット・家電・旅行・トレーニング の4つ。本・投資は停止中）
- 画像は「手作り感のある、やさしい」デザインです。クリーム色の紙（うっすら方眼）、マスキングテープ、蛍光マーカー、手書き風の線・丸・チェック、少し傾いたメモ用紙のカードで構成します
- フォントは、見出しが手書き風の **Klee One**、本文が丸ゴシックの **Zen Maru Gothic** です（どちらも SIL Open Font License。`assets/fonts/` に同梱）
- 配色は `theme` で全ジャンル共通の3系統です（紙 `#FBF6EC`／紺のインク `#2B3A55`／赤系 `#D9472B` は価格と購入導線だけ）。マーカーとテープは淡い色です
  - 文字と紙のコントラスト比は 10.6、赤系と紙は 4.0、ボタンの白文字と赤系は 4.3
  - 紺（青系）の文字は信頼感（Labrecque & Milne, 2012）、赤い価格表示は「お得」に感じられやすい（Puccinelli et al., 2013）、コントラストが高いほど好ましく評価されやすい（Reber, Winkielman & Schwarz, 1998）
- 改行は、読点や助詞（が・を・の など）のあとを優先し、「Wi-Fi」などの英数字の言葉は途中で切りません。句読点が行頭に来ないようにしています
- リールは、スライドを少し小さくして上寄せにし（下のキャプションや右のボタンに隠れないように）、各スライドをゆっくりズームさせながらつなぎます

## セットアップ

### 1. 楽天

1. [楽天アフィリエイト](https://affiliate.rakuten.co.jp/) に登録し、**アフィリエイトID**を控える
2. [Rakuten Developers](https://webservice.rakuten.co.jp/app/list) でアプリを登録し、**アプリID**と**アクセスキー**を控える。"Allowed websites" には GitHub Pages のドメイン（例 `kapibara1812.github.io`）を、APIアクセススコープには「楽天市場API」を設定する
   - 2026年の新API（`openapi.rakuten.co.jp`、IchibaItem/Search 20260701 版）に対応しています。APIのURLが変わった場合は、環境変数 `RAKUTEN_ITEM_SEARCH_URL` で上書きできます

### 2. Instagram

1. Instagram アカウントを**プロアカウント（ビジネスまたはクリエイター）**に切り替える
2. [Meta for Developers](https://developers.facebook.com/) でアプリを作成し、Instagram のユースケースの「**API setup with Unified login**」で設定を作る（ビジネスポートフォリオが必要。無料）
3. アクセス許可 `instagram_basic`・`instagram_content_publish`・`instagram_manage_insights` で設定を作り、投稿用アカウントを追加して**システムユーザーアクセストークン**（`EAA...`、無期限）と **Instagram ユーザーID**（`1784...`）を取得する
4. トークンの種類から接続先を自動で選びます（`EAA...` → `graph.facebook.com`、`IGAA...` → `graph.instagram.com`）。変えたい場合は `IG_API_BASE` で上書きできます

### 3. Claude

[Claude Console](https://platform.claude.com/) で API キーを発行します。使うモデルは `claude-opus-5-5` です。1投稿あたりの費用は数円程度です。

### 4. GitHub

1. **Settings → Pages**: Source を「Deploy from a branch」にし、`main` ブランチの `/docs` フォルダを選ぶ
   → 公開URL（例 `https://<ユーザー名>.github.io/sns-affiliate`）を **Instagram のプロフィールリンク**に設定する
2. **Settings → Secrets and variables → Actions** に次を登録する

| 種類 | 名前 | 内容 |
|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | Claude API キー |
| Secret | `RAKUTEN_APP_ID` | 楽天アプリID |
| Secret | `RAKUTEN_ACCESS_KEY` | 楽天アクセスキー（必須） |
| Secret | `RAKUTEN_AFFILIATE_ID` | 楽天アフィリエイトID |
| Secret | `IG_USER_ID` | Instagram ユーザーID |
| Secret | `IG_ACCESS_TOKEN` | Instagram 長期アクセストークン |
| Variable | `PUBLIC_BASE_URL` | GitHub Pages のURL（末尾の `/` は不要） |
| Variable | `IG_ACCOUNT_NAME` | 画像に入れるアカウント名（`@` は不要。任意） |
| Variable | `SITE_TITLE` | 一覧ページのタイトル（任意） |
| Variable | `USE_PRODUCT_IMAGE` | `true` にすると商品画像をカードに載せる（下の注意を参照） |

3. **Actions → daily-post → Run workflow** から、まず `publish` のチェックを外して実行し、生成された画像と文面を確認する
4. 問題がなければ、以降は毎日 20:07 JST に自動投稿されます。インサイトは毎朝 6:23 JST に `data/insights.csv` へ記録されます

## ローカルで動かす

```bash
pip install -r requirements.txt
export PYTHONPATH=src

python -m sns_affiliate demo                     # API キーなしでサンプル画像を作る → data/demo.jpg
python -m sns_affiliate prepare                  # inbox に写真があればそれを、なければ自動選定で投稿を作る
python -m sns_affiliate prepare --no-inbox --mode roundup --genre travel  # inbox を使わず、まとめ投稿を作る
python -m sns_affiliate demo --roundup           # まとめ投稿のサンプル画像 → data/demo/
python -m sns_affiliate publish                  # 準備済みの投稿を Instagram に公開する
python -m sns_affiliate insights                 # インサイトを取得して CSV に出力する
python -m sns_affiliate site                     # 一覧ページを作り直す
python -m pytest -q                              # テスト
```

## 商品の選び方

- 楽天市場をレビュー件数の多い順に検索し、レビュー `MIN_REVIEW_COUNT`（既定 30件）以上、評価 `MIN_REVIEW_AVERAGE`（既定 4.0）以上の商品だけを候補にします
- `REPOST_COOLDOWN_DAYS`（既定 90日）以内に投稿した商品は選びません
- 上位5件からランダムに1件選ぶので、毎回同じ商品にはなりません

## 法令・規約で守っていること（必ず読んでください）

- **ステマ規制（景品表示法）**: キャプションの冒頭に `【PR】`、ハッシュタグに `#PR`、画像に「PR」ラベル、一覧ページに広告表記を自動で入れます。消さないでください
- **体験談を作らない**: 体験談は `memo.txt` に本人が書いた感想だけを元にします。感想がない投稿では体験談を書かないよう Claude に指示しています
- **写真の位置情報**: 投稿用の画像は位置情報などのメタデータを含まない形で書き出し、使い終わった元の写真は削除します。ただし公開リポジトリにアップロードした時点で履歴には残るため、撮影時にカメラの位置情報をオフにしてください
- **投資ジャンル**: 銘柄の推奨、利益の保証、断定的な予想を禁止し、「学ぶための本」として紹介するように指示しています（金融商品取引法への配慮）
- **商品画像**: 既定では商品画像を使わず、文字だけのカードを作ります。商品画像を使う場合は、楽天アフィリエイトの規約とショップの画像の権利を確認したうえで `USE_PRODUCT_IMAGE=true` にしてください
- **Instagram の規約**: 自動投稿は公式の Content Publishing API で行います。投稿は24時間で100件までという API の上限がありますが、このツールは1日1件です。いいねやフォローの自動化はしません（規約違反でアカウント停止の対象になります）

## 収益を伸ばす運用のコツ

1. **最初の1〜2週間は手動で確認する**: `publish` を外して生成し、文面を見てから公開する
2. **`data/insights.csv` で `saved`（保存数）と `shares`（シェア数）を見る**: どちらもリーチを伸ばす指標です。数値の良いジャンルとキーワードを `genres.json` で増やし、悪いものを減らします
3. **楽天の成果レポートと照合する**: 楽天アフィリエイトの管理画面で、売れた商品と投稿番号を突き合わせ、売れるジャンル・価格帯に寄せる
4. **リールとストーリーズを手動で足す**: 自動投稿のフィード画像でアカウントの土台を作り、伸びた商品だけリールにすると効率が良いです
5. **楽天のイベントに合わせる**: お買い物マラソンや楽天スーパーSALEの期間は、手動で `prepare` を追加実行して投稿を増やす

## 構成

```
config/genres.json          ジャンル定義（キーワード・ハッシュタグ・配色）
config/a8_programs.json     A8.net の案件（自分で作成。example を参照）
src/sns_affiliate/
  inbox.py                  自分の写真（inbox/）の読み込み
  rakuten.py                楽天市場商品検索API
  a8.py                     A8.net 案件の読み込み・選定・禁止表現チェック
  caption.py                Claude による投稿文生成（構造化出力）
  image_card.py             投稿画像の生成（Pillow）
  site.py                   プロフィールリンク用の一覧ページ
  instagram.py              Instagram への投稿・インサイト取得・トークン更新
  pipeline.py               全体の流れ
  __main__.py               CLI
inbox/                      自分で撮った写真と memo.txt を置く場所
data/posts.json             投稿履歴（自動更新）
data/insights.csv           インサイト（自動更新）
docs/                       GitHub Pages（一覧ページと画像）
.github/workflows/          毎日の投稿・インサイト取得・テスト
```
