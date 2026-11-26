# 利益発見ツール2（Mercari arbitrage sourcing tool のコピー）

> **これは「利益発見ツール2」**: 2026-11-14 に `~/program/rieki-hakken`（元の「利益発見ツール」）から作った独立コピー。
> - 場所: `/Users/apple/program/rieki-hakken-2`。ローカル画面は **127.0.0.1:8766**（元は 8765。同時に起動できる。環境変数は `RIEKI2_PORT`）
> - **2026-11-18 から public リポジトリ `takenoriosaka/rieki-hakken-2`（main）で仲間（物販ONE生）に配布**（下の「仲間への配布」）。
>   履歴は引き継がず新しい初回コミットで公開（それ以前の .git は `~/program/.rieki-hakken-2.git-backup-20261118`）。
> - 起動: デスクトップの `利益発見ツール2.command`（中身は start.command を呼ぶだけ）。アイコンは青（元は緑）
> - main.py 本番実行の自動 git push はなし。元の利益発見ツールのファイルには触れないこと

## 仲間への配布（2026-11-18 追加）
- 仲間に送る1行（ターミナルに貼る）: `bash <(curl -fsSL https://raw.githubusercontent.com/takenoriosaka/rieki-hakken-2/main/setup_for_friend.command)`
  → ~/rieki-hakken-2 に clone（ログイン不要）・uv で Python 3.12 venv・Chromium・ブランド一覧のパスワード入力・デスクトップ「利益発見ツール2.command」（青）・8766 で起動。
  元の利益発見ツール（~/rieki-hakken・8765・「利益発見ツール」）と同じ Mac に入れても衝突しない。テスト用の環境変数は RIEKI2_DIR / RIEKI2_DESKTOP / RIEKI2_BRANCH / RIEKI2_BRANDS_PASSWORD / RIEKI_SKIP_PLAYWRIGHT / RIEKI_NO_LAUNCH。
- `start.command` は起動前に git fetch → 新版があれば `git pull --ff-only`。`.rieki_friend_mode`（仲間の Mac だけ。本人の Mac に置かないこと）があれば追跡ファイルの変更を stash してから更新。
  本人の Mac（マーカーなし）は未コミット変更に触れない（ff できなければ警告だけ）。
- **ブランド一覧（物販ONEのブランド一覧の書き写し）は暗号化して配る**: `brands.enc`（scrypt＋AES-256-GCM。中身は course_brands.json のブランド名・英字・グループ・ジャンルだけ）。
  平文の course_brands.json・course_genre_notes.*・my_list.json は絶対にコミットしない（.gitignore 済み）。
  - 本人: course_brands.json を直したら `./venv/bin/python export_brands.py` → `git add brands.enc` → commit → push。パスワードは `.brands_password`（git 管理外・600）。
  - 仲間: セットアップ時に1回だけパスワードを聞く（`set_brands_password.command`、osascript の hidden answer。3回まで・「あとで」可）。合っていれば `.brands_password` に保存し取り込む。
    以後 start.command（`sync_brands`、仲間の Mac だけ）が brands.enc の SHA-256（`.brands_enc.sha256`）が変わったときだけ `brands_sync.py --auto` で取り込む。パスワード未入力なら起動時に1回入力画面（「あとで」でそのまま起動）。
  - 取り込みは `my_list.import_course_new_only`: 取り込んだ名前（正規化）を my_list.json の `course_imported` に記録し、記録済み・同名ありは足さない。
    仲間が追加・編集・削除・オン/オフした登録は上書きも復活もしない。足した分はオフ（ルイヴィトンだけオン）。パスワード違い・未入力でもツールは起動する（一覧が空なだけ）。

## プロジェクト概要
ヤフオク・メルカリ・セカストから転売案件を自動スキャンし、Webダッシュボードで確認するPythonツール。
（旧: メールで生徒に配信する仕組みだったが、ダッシュボード確認に一本化。メール配信機能は撤去済み）

## 実行方法
```bash
cd /Users/apple/program/rieki-hakken-2
source venv/bin/activate
python main.py --dry-run   # DB保存・ダッシュボード更新なしでスキャン確認
python main.py             # 本番実行（スキャン→DB保存→ダッシュボード再生成。git push はしない）
python generate_dashboard.py --open  # ダッシュボードHTMLだけ再生成してブラウザで開く
```

## 現在の状態
- スキャン: ローカル画面（app.py、127.0.0.1:8766）で使う
- `docs/index.html` の公開ダッシュボード（GitHub Pages）への自動反映は、利益発見ツール2 では行わない

## 未解決の課題
- **腕時計が0件問題**: オメガ・タグホイヤー・カルティエ時計が前回も今回も0件
  - 原因候補①: 価格帯バケット化（PRICE_BUCKET=10,000）で相場が取れていない
  - 原因候補②: `required_words`（時計・腕時計など）が厳しすぎてフィルタで落ちている
  - 原因候補③: `require_model_number=true` で型番照合できず全件スキップ
  - 調査方法: `python main.py --dry-run` を実行してログを確認する

## ファイル構成
- `main.py` - メインスキャン処理（スキャン→DB保存→ダッシュボード再生成。自動publishなし）
- `config.json` - キーワード・価格・カテゴリ設定
- `analyzer.py` - 相場取得・案件計算
- `model_extractor.py` - 型番抽出
- `database.py` - SQLite読み書き（スキーマ・マイグレーション含む）
- `generate_dashboard.py` - ダッシュボードHTML生成
- `docs/index.html` - 生成済みダッシュボード（元ツールからのコピー。利益発見ツール2 では公開しない）
- `arbitrage.db` - SQLiteキャッシュ・履歴DB（gitignore済み）
- `ocr_reader.py` - 写真のタグから型番を無料で読む（Mac 内蔵の OCR = Apple Vision。説明文にも型番が無い出品が対象。結果は photo_ocr_cache）
- `photo_reader.py` - 写真AI確認（有料・Claude API。既定オフ）。OCR でも型番が無い出品のタグ読み取りと、案件の傷・汚れ判定。結果は photo_read_cache / photo_inspect_cache
- `set_api_key.command` - Claude の API キーを `.env` に登録する（ダブルクリック。キー自体は含まない）
- `.env` - `ANTHROPIC_API_KEY`（写真から型番を読む機能を使うときだけ。gitignore済み。キーを絶対にコミットしないこと）

## 写真から型番を読む機能（2026-10 追加）
- `.env` に `ANTHROPIC_API_KEY` があり、かつ `.rieki_friend_mode` が無いときだけ動く（それ以外は静かに無効。anthropic も import しない）。
- 読んだ文字列は必ず `model_extractor.extract()` に通し、型番形式に当てはまるものだけ採用する。
- 設定は config.json の settings（`photo_read_model` 既定 claude-sonnet-5-5、`photo_read_max_yen_per_scan` 既定 1000円 など）。
- ローカルの `.git/hooks/pre-commit`（git 管理外）で API キーの文字列（sk-ant… で始まるもの）を含むファイルと `.env` のコミットを止めている。
- 2026-11 追加: 同じ API 呼び出しで写真の傷・汚れも判定する（5段階: 新品同様/目立った傷なし/やや傷あり/傷あり/状態悪い。
  `photo_reader.inspect_deals` → 結果は photo_inspect_cache（URL＋写真URL一覧＋判定基準の版 `_INSPECT_VERSION` で照合））。
  タイトルの状態と悪いほうを取り（`condition_checker.combine`）、利益を計算し直す（`analyzer.apply_photo_condition`、`main._check_photo_conditions`）。
  ローカル画面のステップ4「写真AI確認（有料・Claude API）」のチェックで1回ごとにオン/オフ（`settings.photo_check_enabled`、2026-11-06 から既定オフ）。料金上限は品番・状態で共有。

## 写真のタグ読み取り（無料・Mac内OCR、2026-11-06 追加）
- 型番照合でタイトル・説明文に型番が無い出品は、まず `ocr_reader.read_listings` で写真を OCR（Apple Vision の
  VNRecognizeTextRequest、accurate、ja-JP+en-US、言語補正なし）し、読めた文字を説明文と同じく `model_extractor` で照合する。
  型番は1行ずつ照合し、見つからなければ OCR の読み間違い（数字に隣接する O→0・I/l→1、タトラスの先頭 I→L）を直した行でも探す。
  モデル名は写真にブランド名も写っているときだけ採用。
- 使えるのは macOS で `pyobjc-framework-Vision`（requirements.txt。darwin のみ）が入っているときだけ。料金なし・ログインなし。
- キャッシュ photo_ocr_cache: キー＝出品URL＋写真URL一覧＋`_OCR_VERSION`。OCR の全文を保存し、照合はキャッシュからも毎回やり直す
  （model_extractor の形式を増やしたら OCR し直さずに反映）。
- ローカル画面のステップ4「写真のタグ読み取り（無料・Mac内OCR）」（`settings.photo_ocr_enabled`、既定オン）。
- **有料の写真AI確認（photo_reader）は既定オフ**（`settings.photo_check_enabled` 既定 False。画面の「写真AI確認（有料・Claude API）」は
  毎回オフから始まり、前回の選択も復元しない）。オンにしたときだけ OCR でも型番が無い出品を Claude で読み、案件の傷・汚れも判定する。
  既定の設定では Anthropic API は一切呼ばれない。
- 傷・汚れのローカル判定は `local_inspector.py`（下の「写真で傷・汚れ確認（無料・Mac内AI）」）。`ocr_reader.inspect_deals_local` からも呼べる。

## 写真で傷・汚れ確認（無料・Mac内AI、2026-11-06 追加）
- `local_inspector.py`: 案件の写真を Mac 内の Ollama（http://127.0.0.1:11434、既定モデル `qwen3-vl:8b-instruct`）に1枚ずつ見せ、
  傷・汚れの種類ごとに yes/maybe/no で答えさせて（英語の指示・JSON スキーマ指定）、重さ×yes の数から5段階のランクにする（`_aggregate`）。
  結果は有料版と同じ形（＋`source: "local"`, `model`）で、`condition_checker.combine`・`analyzer.apply_photo_condition` をそのまま使う。
- 有料の写真AI確認もオンのときは有料の判定を優先し、判定できなかった案件だけ無料で見る（`main._check_photo_conditions`）。
- キャッシュ photo_local_inspect_cache（キー＝`_LOCAL_VERSION`＋モデル名＋写真URL一覧）。有料版のキャッシュとは別。
- ローカル画面のステップ4「写真で傷・汚れ確認（無料・Mac内AI）」（`settings.photo_local_check_enabled`、既定オフ）。
  Ollama が入っていない Mac（知り合いの Mac 等）では項目ごと出さない。入っていて止まっていれば `ollama serve` を起動してみる
  （ログは logs/ollama_serve.log）。インストールはしない。
- 本人の Mac: Ollama は Homebrew（`brew install ollama`、/opt/homebrew/bin/ollama）。ログイン項目・brew services には登録していない。
- タグの文字の読み取り（`local_inspector.read_listings`、`settings.photo_local_read_enabled`、既定オフ）: OCR でも型番が無い出品の
  タグ写真を AI に書き写させ、`ocr_reader.extract_model` で照合する。

## 仕入れ先 Yahoo!ショッピング（2026-11-06 追加）
- `scrapers/yahoo_shopping.py`（仕入れ先キー `yahoo_shopping`）。ログインなし・Cookie なしの GET で、検索結果 `/search/<検索語>/0/<ページ>/` と
  商品ページ `store.shopping.yahoo.co.jp/<店>/<商品>.html` の `__NEXT_DATA__` を読む。公式 API（Client ID が必要）は使っていない。
- robots.txt で禁止されている検索パラメータ（pf/pt=価格、n=件数、view、ship 等）と `/searchbff/*/search/postageInfo` は使わない。
  使うのは `used`（1=中古 / 2=新品）・`X=2`（安い順）とページ番号だけ。価格の範囲は安い順に読んで上限を超えたら打ち切る（最大10ページ）。
- 既定は中古のみ（`settings.yahoo_shopping_condition`、既定 "used"。画面の「Yahoo!ショッピングは新品も含める」で "all"）。
- 仕入れ値は **表示価格＋送料**（PayPay ポイント・クーポンは数えない）。送料無料でない出品は、タイトルの照合のあと
  `main.fill_shopping_postage` → `yahoo_shopping.fill_postage` で商品ページの `postage.fee`（東京都あて）を足す。取れなければ
  `settings.yahoo_shopping_default_postage`（既定 1,000円）を仮に足す。在庫なし・販売終了はここで除く。`Item.shipping` / `base_price`。
- 中古ランク（検索結果 labels.hasUsed20/40/60/80 ＝ 目立った傷や汚れなし/やや傷や汚れあり/傷や汚れあり/全体的に状態が悪い）が
  タイトルの状態より悪ければ、その言葉をタイトルの末尾に【】で足して condition_checker に効かせる。
- 写真は `https://item-shopping.c.yimg.jp/i/l/<画像ID>`（長辺600px）。説明文・写真は商品ページを1回読んだら控えて再取得しない。

## フリーリサーチモード（2026-11-06 追加）
- `free_research.py`。ローカル画面のヘッダー「フリーリサーチ」。登録済みのモデル・型番に縛られず、ブランド（自由入力）×
  ジャンル（`GENRES`: セットアップ・スーツ/ジャケット/コート/ダウン/その他アウター/トップス/ボトムス/ワンピース/バッグ/靴/ネクタイ/小物＋自由入力。2026-11-16 に「その他アウター」(key outer)と「ネクタイ」(key tie。小物から独立)を追加）×
  仕入れ値の範囲（送料込み）× メンズ/レディース/指定なし で全仕入れ先を検索する。`/api/research` に `mode: "free"`。
- **ジャンルはメルカリのカテゴリに合わせる（2026-11-16）**: メルカリの小分類1つ = ツールのジャンル1つ（重ならない）。
  各ジャンルの `mercari`（{"men","women"} のカテゴリID。jp.mercari.com が読む `api.mercari.jp/master/v2/datasets/item_categories`
  で確認）と `hint`（画面のチップの title に「メルカリの「…」」で出す。フリーリサーチ・マイブランド登録）。
  アウター（メルカリの「ジャケット・アウター」）は小分類の名前で振り分け: 名前に「コート」→コート（ポンチョ・ケープコートも）/
  ダウン・キルティング→ダウン・中綿 / 名前に「ジャケット」＋MA-1（テーラード・ノーカラー・レザー/ライダース・Gジャン・ミリタリー・
  MA-1/フライト・ナイロン・フリース/ボア・ビジネスジャケット）→ジャケット / 残り（ジャンパー・ブルゾン・マウンテンパーカー・スタジャン・
  スカジャン・カバーオール・その他）→その他アウター。タイトルでは `outer_class()` が上から順に1つだけ決める（ダウン→コート→
  ジャケットの小分類名〔MA-1・ライダース・Gジャン等〕→ブルゾン系→ただの「ジャケット」）。トップス・ボトムス・ワンピースはアウターの語があれば除く。
  ネクタイ = メンズ>小物>ネクタイ＋フォーマル小物のボウタイ・礼服用ネクタイ。ネクタイピン・カフス・レインコートは小物（メルカリどおり）。
  スーツベスト・フォーマルシャツ→トップス、スラックス→ボトムス、サロペット/オーバーオール→ボトムス、ジャンパースカート→ワンピース。
  レディースの「Gジャン・デニムジャケット・カバーオール」は1つの小分類なのでカテゴリではジャケット（タイトルの「カバーオール」はその他アウター）。
- 相場: メルカリ売り切れ（ログインなし）。組み込みの12ジャンルは **「ブランド名だけ」をメルカリのカテゴリID で絞って検索**
  （URL の `category_id=1,2,3`。メンズ/レディースを選べばその性別のIDだけ・性別の語は付けない。キャッシュのキーにIDを含む）。
  カテゴリで絞った売り切れはタイトルのジャンルの語を見ない（`title_fits(by_category=True)`。はっきりコート/ダウンが別のアウターの
  カテゴリにある物と、セットアップ・バッグ等の conflict だけ除く）。画面の「メルカリで見る」にも同じ category_id を付ける。
  `settings.free_mercari_category_filter`=false で従来の方式。自由入力・型番のジャンル（mercari なし）は従来どおり
  「ブランド + ジャンルの検索語（+ メンズ/レディース）」で取り、タイトルにブランド・
  ジャンルの語があり、別アイテム・キッズ・付属品・まとめ売り・「〜風」・逆の性別でないものだけ。上下10%を除いた中央値・四分位。
  信頼度: 高=20件以上かつ (Q3−Q1)/中央値 ≤0.6、中=8件以上かつ ≤1.0、低=それ以外（3件未満は相場なし）。売り切れは
  `free_market_cache` テーブル（DB）に price_cache_hours 保存し、絞り込みは毎回やり直す。
- **服⇔小物の取り違え除外（2026-11-26）**: 出品タイトルの「ビジネス スーツ」等の検索対策の語で、ベルト・ネクタイがセットアップ・スーツの案件に混ざった対策。`accessory_item`（服8ジャンル: ベルト/ネクタイ・クラヴァット/タイピン・カフス/財布/バッグ/靴/アクセサリー/マフラー・帽子、セットアップはYシャツ等も）: 小物の語が服の語より前・服の語が無い・セットアップで上下の語なし → 除く。「〜付き/付属/セット/欠品/柄/プリント」「ウエストベルト・共布」「＋ネクタイ」は付属扱いで残す。逆向き `garment_item`（バッグ/靴/ネクタイ/小物）: 服の語が小物の語より前なら除く（後ろだけなら検索対策とみなして残す。ネクタイ・小物の conflict にあった服の語はこちらへ移した）。title_fits の中で効くので model_genre・売り切れ（カテゴリ絞り込み）でも同じ。
- 性別: `detect_gender`（タイトル→説明文→タトラス品番 MTA/LTA→スカート等のアイテム→紳士服サイズ A5 等）。不明は除かずに表示。
- 注目ポイント: `FEATURES`（利益商品共有 seed.json の特徴タグのうち文字で読めるもの）。売り切れの中でその特徴あり/なしの中央値を比べる。
- 型番: config.json に同じ ブランド×ジャンル のキーワードがある組（タトラス×ダウン 等）だけ、タイトル・説明文・写真のOCRで型番が
  取れたら型番の相場（analyzer.get_market_price、3件以上）に切り替える（信頼度 高）。
- ブランドの英字表記（`EXTRA_ALIASES`）は model_extractor.BRAND_ALIASES に無いものだけ実行中に足す。仕入れ先は `EN_QUERY` の英字名でも検索する。
- 入力候補: 登録済みのブランド＋ ~/program/rieki-shohin-share/data/seed.json の件数・平均利益（投稿者名は使わない）。

## マイブランド登録（旧名: 仕入れリスト。2026-11-14 追加、同日シンプル化）
- 自分で育てるブランド・ジャンルのリスト。ローカル画面のヘッダー「マイブランド登録」（2026-11-14 に「仕入れリスト」から改名。内部名・ファイル名は my_list のまま）。保存は **`my_list.json`**（.gitignore 済み・共有しない）。
  処理は `my_list.py`、画面は `local_ui/my_list.js`（research.js の後に読み込み）、API は app.py の `/api/mylist*`
  （GET 一覧 / POST entry 追加 / PUT・DELETE entry/<id> / POST toggle {ids|group, enabled}（group="" で全件） / POST genre {label[, remove]} / POST defaults {group, genres}）。
- （2026-11-14〜15 の旧仕様。11-16 に下の7項目へ拡張）**画面で登録・編集するのは3つだけ**（ユーザー要望「シンプルが良い」）: ブランド（入力候補 datalist〔OPTIONS.free.brands、登録済みは除く〕）・
  ジャンル（チップで複数。小さな「＋ ジャンルを作る」でその場で追加 → `custom_genres`）・上限価格（`price_max`、送料込み、空欄＝リサーチ画面の範囲）。
  グループ・英語のつづり・別名・下限価格・最低利益・メモ・グループの既定ジャンルの編集欄は画面から撤去（JSON・API には残る）。
- **2026-11-16 ユーザー要望で7項目に**: ブランド・ジャンル・型番（`model_numbers`、「、」かスペース区切り・大文字に）・モデル名（`models`、「、」区切り）・
  下限金額（`price_min`）・上限金額（`price_max`）・検索数（`per_source`: null=リサーチ画面の取得件数 / 20 少なめ / 50 普通 / 80 多め）。
  キーが無い昔の分も読める。型番・モデル名があるブランドは `list_pairs` の `terms` になり、`free_research.model_genre` で
  「ブランド + 型番/モデル名」を1つずつ検索し、タイトルにその語（登録済みモデル名なら英字名・表記ゆれも。空白・ハイフン・長音は無視）がある出品だけ残す
  （ジャンルを1つだけ選んでいればそのジャンルの別アイテム除外・性別判定・型番の相場の切り替えも使う）。同じ出品は URL で1件に。
  下限・上限金額はブランドの指定がリサーチ画面の範囲より優先（空欄の側は画面の値）。検索数はその組だけ `search_items_per_source` を上書き。
  フリーリサーチ（マイブランド含む）は FOCUS_MODELS（ルイヴィトンの主力10モデル）の制限を通らないので、登録した型番・モデル名はそのまま調べられる。
- 英語のつづりは自動: 追加・改名のとき `auto_name_en`（`canonical_brand` で寄せた名前の free_research.EN_QUERY、無ければ
  BRAND_ALIASES/EXTRA_ALIASES の英字表記）を `name_en` に入れる。改名したら aliases は空に戻す。昔保存した name_en・aliases はそのまま使う。
- 1件の JSON = name・name_en・aliases・group（新規は「自分で追加」）・genres・enabled・memo・price_min/price_max/min_profit・origin 等。
  重複は `normalize_name`（中黒・空白・ヴ/ビ等を無視。name_en とも照合）で防ぐ。
- ジャンル未設定のブランドは、グループの既定のジャンル（`group_defaults`。既定値 `DEFAULT_GROUP_GENRES`: ハイブランド=バッグ・小物、
  その他小物=小物・バッグ、紳士=セットアップ・ジャケット・コート・ネクタイ、レディース=ワンピース・トップス・ボトムス・コート、他（自分で追加 含む）=アパレル）で調べる。
  一覧の行には「ジャンル：おまかせ（…）」と表示。
- 「マイブランドでリサーチ」: `/api/research` に `mode: "list"`（`list_ids`。`list_group` は互換のため残すが画面からは送らない）。
  調べるブランドは「オンのブランドすべて」か「これだけ調べる」の1ブランド（グループ指定は撤去。昔の localStorage `rieki-mylist-target-v1` は起動時に消す）。
  `my_list.to_free_request` が `list_pairs`（ブランド×ジャンル＋ブランドの上限価格）を作り、フリーリサーチ（`free_research.scan`）で1組ずつ順番に調べる
  （should_stop で中止可、1組の失敗では止めない）。上限価格はリサーチ画面の上限より優先、下限・最低利益はリサーチ画面の値（昔の price_min/min_profit は使わない）。
  ブランド名は `canonical_brand` でツールが知っている名前に寄せる（ルイ・ヴィトン→ルイヴィトン）。
  name_en・aliases は `register_aliases` が実行中だけ BRAND_ALIASES / EXTRA_ALIASES / EN_QUERY に足す（次の実行の前に元に戻す。英字2文字以下は照合に使わない）。
- 学校のブランド一覧の取り込み: `./venv/bin/python import_brands.py <course_brands.json>`（`my_list.import_course`）。取り込んだ分はオフ
  （ルイヴィトンだけオン）、同じブランドは追加しない（英字表記・ジャンルが空なら補うだけ）。group・memo も保存（画面には出ない）。何度実行しても大丈夫。
- 画面の作り（my_list.js）: 上から 説明カード（🌱 1行。たたんだ状態は localStorage `rieki-mylist-intro-collapsed-v1`）→
  追加の欄（ブランド＋上限価格＋「＋ 追加」、その下にジャンル。Enter でも追加。追加した行は少し光り、成功メッセージ。alert/confirm は使わない）→
  件数 → 検索・すべて/オンだけ/オフだけ → 「すべてオン」「すべてオフ」（全件）→ 1本の一覧（グループ分けなし。行ごとに「リサーチする / しない」スイッチ・
  ブランド・上限 ¥X か「上限なし」・ジャンル・✏️ 編集〔その行の中で3項目を編集。Enter で保存・Esc でやめる〕・🗑 削除〔その場で確認〕・これだけ調べる）。
- 見本: my_list.json が空なら最初に1回だけ `SAMPLE_ENTRIES`（ルイヴィトン=バッグ、ポールスミス PAUL SMITH=ジャケット。オン）を入れ、
  `"seeded": true` を保存（消しても入れ直さない）。見本は `"sample": true`（画面に「見本」バッジ＋「消しても大丈夫です」）、編集すると外れる。
- 条件を選ぶ・フリーリサーチの画面の上に「マイブランド：登録 N件（オン M件） ✏️ 登録・編集する」（`[data-ml-link]`、押すとマイブランド登録へ）。
- モデル・型番のリサーチ（条件を選ぶ）とフリーリサーチはそのまま使える。
