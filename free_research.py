"""
フリーリサーチモード（ローカル画面 app.py から使う）

登録済みのモデル名・型番に縛られずに、
  ブランド（自由入力）× ジャンル（セットアップ・コート等の自由な選択）× 仕入れ値の範囲 × メンズ/レディース
で仕入れ先を検索し、メルカリの売り切れ（ログインなし。scrapers.mercari.get_sold_items）から
「ブランド + アイテムの種類（+ メンズ/レディース）」の相場を出して利益を見積もる。

型番で照合する通常のモードより大まかな相場なので、案件ごとに
  ・相場の根拠（売り切れの件数・中央値・中央50%の範囲＝四分位範囲）
  ・信頼度（高/中/低）
  ・注目ポイント（利益商品共有の特徴タグ: ダブルボタン・カシミヤ・派手裏地 等）と、売り切れの中での
    その特徴あり/なしの中央値の比較
を付ける。タイトル・説明文・写真（無料の Mac 内 OCR）で型番が見つかった出品は型番の相場に切り替える。

ログイン・Cookie は使わない。有料の API（Anthropic）は既定では呼ばない（写真の確認は main と同じ設定に従う）。
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import unicodedata
from datetime import datetime, timedelta

import analyzer
import condition_checker
import database
import local_inspector
import model_extractor
import ocr_reader
import photo_reader
from models import Deal, MarketPrice

SEED_PATH = os.path.expanduser("~/program/rieki-shohin-share/data/seed.json")

# ──────────────────────────────────────────────────────────────────────────────
# ジャンル（2026-11-16 メルカリのカテゴリに合わせて作り直し）
# ──────────────────────────────────────────────────────────────────────────────
# メルカリのカテゴリ（ファッション > メンズ/レディース > 中分類 > 小分類）の小分類1つ = ツールのジャンル1つ（重ならない）。
# アウター（メルカリの「ジャケット・アウター」）の分け方は、メルカリの小分類の名前で決める:
#   名前に「コート」→ コート / ダウン・キルティング（中綿）→ ダウン・中綿 / 名前に「ジャケット」（MA-1 も含む）→ ジャケット /
#   それ以外（ジャンパー・ブルゾン、マウンテンパーカー、スタジャン、スカジャン、カバーオール、その他）→ その他アウター
# key: 画面と通信する識別子 / label: 表示名 / hint: 画面のチップに出す「何が入るか」（メルカリの小分類名） /
# queries: 仕入れ先の検索に使う語（ブランド名の後ろに付ける）/
# match: タイトルにどれかが必要な語（正規表現。NFKC・大文字化したタイトルに当てる。アウター4ジャンルは outer_class で判定）/
# conflict: タイトルにあれば別ジャンルとみなして除く語 / seed: 利益商品共有の genreMajor /
# mercari: メルカリ売り切れ（相場）の検索で絞り込むメルカリのカテゴリID {"men": [...], "women": [...]}
#   （jp.mercari.com の /master/v2/datasets/item_categories で 2026-11-16 に確認したID）
GENRES: list[dict] = [
    {"key": "setup", "label": "セットアップ・スーツ", "icon": "🤵", "queries": ["セットアップ", "スーツ"],
     "hint": "スーツ（上下）・セットアップ・タキシード・礼服",
     "match": r"セットアップ|スーツ|SUIT|上下|スリーピース|ツーピース|[23]ピース|SET\s?UP|タキシード|TUXEDO",
     "conflict": r"スーツケース|キャリー|ウェットスーツ|水着|スイムスーツ|ジャージ|スウェット|パジャマ|ルームウェア|"
                 r"(?:ジャケット|パンツ|スカート|スラックス)\s*のみ|単品|単体|スーツカバー|ガーメント|ハンガー|"
                 r"ボディスーツ|トラックスーツ|ジャンプスーツ|つなぎ|オールインワン|サロペット|スノー|スキー|ラウンジ|"
                 r"セットアップ用|スーツ用|スーツに",
     "seed": "セットアップ・スーツ",
     # メンズ>スーツ のビジネススーツ・カジュアルスーツ・ブラックフォーマル・タキシード・モーニング・燕尾服 /
     # レディース>スーツ・フォーマル・ドレス>スーツ・フォーマル（バッグを除く）・セットアップ
     "mercari": {"men": [367, 1744, 1745, 1746, 1747, 1748],
                 "women": [188, 1571, 1565, 1577, 1579, 1490, 1489, 1491]}},
    {"key": "jacket", "label": "ジャケット", "icon": "🧥", "queries": ["ジャケット"],
     "hint": "テーラード・ノーカラー・レザー/ライダース・Gジャン/デニム・ミリタリー・MA-1/フライト・ナイロン・フリース/ボア",
     "match": r"ジャケット|JACKET",
     # 「スーツ」の語があっても「単品・のみ」ならジャケット（メルカリのビジネスジャケット）
     "conflict": r"セットアップ|上下|スリーピース|ツーピース|ワンピース|スカート|SKIRT|"
                 r"^(?!.*(?:単品|のみ|単体)).*(?:スーツ|SUIT)"
                 r"|(?<![A-Z])BAGS?(?![A-Z])|バッグ|トート|財布|ブーツ|BOOTS|シューズ|スニーカー|ストール|マフラー|手袋",
     "seed": "ジャケット",
     # メルカリの小分類で名前に「ジャケット」がつくもの（ダウン・キルティングは除く）＋ MA-1・ビジネスジャケット
     "mercari": {"men": [314, 315, 317, 316, 320, 322, 321, 1697, 364],
                 "women": [137, 138, 140, 139, 143, 1444, 1443]}},
    {"key": "coat", "label": "コート", "icon": "🧥", "queries": ["コート"],
     "hint": "トレンチ・ステンカラー・チェスター・ピーコート/ウール・ダッフル・モッズ・ロング・ムートン・ファー・ポンチョ/ケープ",
     "match": r"コート|COAT",
     "conflict": r"セットアップ|スーツ|SUIT|上下|ワンピース|スカート|SKIRT"
                 r"|(?<![A-Z])BAGS?(?![A-Z])|バッグ|トート|財布|ブーツ|BOOTS|シューズ|スニーカー|ストール|マフラー|手袋",
     "seed": "コート",
     "mercari": {"men": [325, 1140, 326, 327, 324, 323],
                 "women": [149, 150, 153, 1447, 1129, 146, 152, 151, 1149]}},
    {"key": "down", "label": "ダウン・中綿", "icon": "🧥", "queries": ["ダウン"],
     "hint": "ダウンジャケット・ダウンコート・ダウンベスト・キルティングジャケット/ベスト",
     "match": r"ダウン|DOWN",
     "conflict": r"セットアップ|スーツ|SUIT|上下|ワンピース|スカート|SKIRT"
                 r"|(?<![A-Z])BAGS?(?![A-Z])|バッグ|トート|財布|ブーツ|BOOTS|シューズ|スニーカー|ストール|マフラー|手袋",
     "seed": "ダウン・中綿",
     "mercari": {"men": [318, 334, 1722], "women": [141, 144, 1446]}},
    {"key": "outer", "label": "その他アウター", "icon": "🧥",
     "queries": ["ブルゾン", "マウンテンパーカー", "スタジャン", "スカジャン"],
     "hint": "ジャンパー/ブルゾン・マウンテンパーカー・スタジャン・スカジャン・カバーオール",
     "match": r"ブルゾン|BLOUSON",
     "conflict": r"セットアップ|スーツ|SUIT|上下|ワンピース|スカート|SKIRT|ブーツ|BOOTS|シューズ|スニーカー|"
                 r"バッグ|BAG|財布|手袋|グローブ|GLOVE|キーホルダー|ワッペンのみ",
     "seed": "その他アウター",
     "mercari": {"men": [330, 332, 328, 329, 333, 335], "women": [145, 1445, 147, 148, 155]}},
    {"key": "tops", "label": "トップス", "icon": "👕", "queries": ["シャツ", "ニット", "カーディガン"],
     "hint": "シャツ・Tシャツ・ニット/セーター・カーディガン・パーカー・スウェット・ベスト/ジレ・ポロシャツ",
     "match": r"シャツ|SHIRT|ニット|KNIT|セーター|SWEATER|カーディガン|CARDIGAN|ブラウス|カットソー|パーカー|PARKA|"
              r"スウェット|トレーナー|ポロ|タンクトップ|TEE(?![A-Z])|ジレ|ベスト",
     "conflict": r"セットアップ|スーツ|上下|ワンピース|シャツワンピ|ネクタイ|ニットタイ",   # アウターの語は outer_class で除く
     "seed": "トップス",
     # 全部 + メンズ>スーツ のスーツベスト・フォーマルシャツ・フォーマルベスト
     "mercari": {"men": [30, 365, 1750, 1749], "women": [11]}},
    {"key": "bottoms", "label": "ボトムス", "icon": "👖", "queries": ["パンツ", "スカート"],
     "hint": "パンツ・スラックス・デニム・スカート・サロペット/オーバーオール",
     "match": r"パンツ|PANTS|スラックス|SLACKS|スカート|SKIRT|デニム|DENIM|ジーンズ|JEANS|ジーパン|ショートパンツ|"
              r"キュロット|トラウザー|TROUSERS|チノ|サロペット|オーバーオール|オールインワン",
     "conflict": r"セットアップ|スーツ|上下|ジャケット|コート|ダウン|ワンピース|ジャンパースカート|下着|ボクサー|ブリーフ|水着|"
                 r"アンダーウェア|ショーツ\s*(?:下着|インナー)|Gジャン|ジージャン|SUIT|SET\s?UP|シャツ|SHIRT|バッグ|ポーチ|財布",
     "seed": "ボトムス",
     "mercari": {"men": [32, 366], "women": [13, 14, 163]}},
    {"key": "onepiece", "label": "ワンピース", "icon": "👗", "queries": ["ワンピース"],
     "hint": "ワンピース・ドレス・ジャンパースカート",
     "match": r"ワンピース|ドレス|DRESS|ONE\s?PIECE|ジャンパースカート",
     "conflict": r"セットアップ|スーツ|ドレスシャツ|ウエディング|ウェディング|ONE\s?PIECE\s*(?:漫画|コミック)|尾田",
     "seed": "ワンピース",
     "mercari": {"men": [], "women": [15, 1090]}},
    {"key": "bag", "label": "バッグ", "icon": "👜", "queries": ["バッグ"],
     "hint": "ショルダー・トート・ハンドバッグ・リュック・ボストン・クラッチ・ポーチ等",
     "match": r"バッグ|BAG|トート|TOTE|ショルダー|リュック|バックパック|BACKPACK|ハンドバッグ|クラッチ|ボストン|"
              r"ブリーフケース|ボディバッグ|ウエストポーチ|サコッシュ|ポシェット|ポーチ",
     "conflict": r"ショッパー|紙袋|保存袋|布袋|巾着袋のみ|ノベルティ|付録|ガーメント|バッグチャーム|バッグハンガー|"
                 r"ショルダーストラップのみ|ストラップのみ",
     "seed": "バッグ",
     "mercari": {"men": [34], "women": [20]}},
    {"key": "shoes", "label": "靴", "icon": "👞", "queries": ["靴", "スニーカー", "ブーツ"],
     "hint": "スニーカー・ブーツ・革靴/ローファー・パンプス・サンダル等",
     "match": r"靴|シューズ|SHOES|スニーカー|SNEAKER|ブーツ|BOOTS|ローファー|LOAFER|パンプス|PUMPS|サンダル|SANDAL|"
              r"ミュール|モカシン|ドライビング|革靴|スリッポン|オックスフォード|ストレートチップ|ウイングチップ",
     "conflict": r"シューキーパー|シューツリー|靴べら|靴紐|シューレース|インソール|箱のみ|シューズケース|保存袋のみ|"
                 r"シューズバッグ|靴下|ソックス",
     "seed": "靴",
     "mercari": {"men": [33], "women": [16]}},
    # ネクタイ: メルカリのメンズ>小物>ネクタイ と スーツ>フォーマル小物 のボウタイ・礼服用ネクタイ（タイピンは小物）
    {"key": "tie", "label": "ネクタイ", "icon": "👔", "queries": ["ネクタイ"],
     "hint": "ネクタイ・ボウタイ/蝶ネクタイ（ネクタイピンは小物）",
     "match": r"ネクタイ|NECKTIE|(?<![A-Z])TIES?(?![A-Z])|(?<![ァ-ヶー])(?:ボウ|ニット|クロス|ナロー|シルク)?タイ(?![ァ-ヶー])|"
              r"ボウタイ|BOW\s?TIE|アスコット|ASCOT|クロスタイ|ニットタイ|クラヴァット|クラバット|CRAVATE|蝶ネクタイ|パピヨン",
     "conflict": r"ネクタイピン|タイピン|タイバー|タイクリップ|タイタック|TIE\s?(?:PIN|BAR|CLIP|TACK)|ネクタイ掛け|"
                 r"ネクタイハンガー|ネクタイケース|ネクタイ柄|セットアップ|上下|ボウタイブラウス|"
                 r"タイト|タイツ|タイ製|タイ王国|タイ国",   # 服の語（スーツ・シャツ 等）は garment_item で位置を見て除く
     "seed": "ネクタイ",
     "mercari": {"men": [391, 1755, 1756], "women": []}},
    # 小物: メルカリの 小物（ネクタイ以外）＋ 帽子・アクセサリー・カフス・フォーマル小物（ネクタイ以外）
    {"key": "komono", "label": "小物", "icon": "🧣", "queries": ["財布", "ベルト", "マフラー"],
     "hint": "財布・ベルト・マフラー/ストール・スカーフ・キーケース等・ネクタイピン・カフス・帽子・アクセサリー・サングラス",
     "match": r"財布|ウォレット|WALLET|ベルト|BELT|マフラー|ストール|スカーフ|バンダナ|"
              r"キーケース|カードケース|名刺入れ|コインケース|帽子|キャップ|ハット|手袋|グローブ|カフス|カフリンクス|ハンカチ|"
              r"キーホルダー|キーリング|サングラス|ネックレス|リング|ブレスレット|"
              r"ネクタイピン|タイピン|タイバー|タイクリップ|タイタック|TIE\s?(?:PIN|BAR|CLIP|TACK)|マネークリップ|レインコート|レインポンチョ",
     "conflict": r"ネクタイ(?!ピン)|NECKTIE|ボウタイ|"
                 r"セットアップ|上下|ノベルティ|付録|"
                 r"箱のみ|保存袋のみ|ベルト付き|ベルト付",   # 服の語（ジャケット・スーツ・コート 等）は garment_item で位置を見て除く
     "seed": "小物・アクセサリー",
     "mercari": {"men": [401, 2128, 385, 384, 387, 1775, 394, 390, 7453, 388, 7470, 389, 395, 392, 396, 398, 408,
                         400, 386, 393, 406, 407, 409, 410, 397, 399, 413, 1751, 1753, 1757, 1754, 36, 37],
                 "women": [23, 19, 21]}},
]
GENRE_BY_KEY = {g["key"]: g for g in GENRES}

# ── アウター4ジャンル（ジャケット / コート / ダウン・中綿 / その他アウター）の振り分け ──
# タイトル1つ → どれか1つ（または None）。上から順に見て最初に当たったもの（＝メルカリの小分類の名前の付け方）
OUTER_KEYS = ("jacket", "coat", "down", "outer")
_NOT_OUTER_RE = re.compile(r"ダウンロード|カウントダウン|ボタンダウン|BUTTON\s*DOWN|ダウンライナー(?:のみ|単品)|"
                           r"ジャンパースカート|コーティング|コート紙|レインコート|レインポンチョ")
_OUTER_RULES: list[tuple[str, re.Pattern]] = [
    # ダウン・中綿: ダウンジャケット・ダウンコート・ダウンベスト・中綿・キルティングジャケット/ベスト
    ("down", re.compile(r"ダウン|(?<![A-Z])DOWN(?![A-Z])|中綿|PUFFER|パファー|"
                        r"キルティング\s*(?:ジャケット|ベスト|ブルゾン|ジャンパー|コート|JACKET|VEST)|QUILT(?:ED|ING)\s*(?:JACKET|VEST|COAT)")),
    # コート: 名前に「コート」（トレンチ・ステンカラー・チェスター・ピーコート・ダッフル・モッズ・ムートン・ファー・ポンチョ/ケープ）
    ("coat", re.compile(r"コート|(?<![A-Z])COAT(?![A-Z])|トレンチ|TRENCH|チェスター|ステンカラー|ダッフル|DUFFLE|"
                        r"マッキントッシュ|ポンチョ|PONCHO|ケープ|(?<![A-Z])CAPE(?![A-Z])|マント")),
    # ジャケットの小分類の名前そのもの（ブルゾン等の語より先に見る。例: 「MA-1 ブルゾン」はメルカリでは MA-1）
    ("jacket", re.compile(r"テーラード|TAILORED|ブレザー|BLAZER|ノーカラージャケット|レザージャケット|ライダース|RIDERS|"
                          r"Gジャン|ジージャン|デニムジャケット|ミリタリージャケット|(?<![A-Z0-9])MA-?1(?![0-9])|"
                          r"フライトジャケット|ボンバー|BOMBER|(?<![A-Z0-9])N-?3B|(?<![A-Z0-9])M-?65(?![0-9])|"
                          r"ナイロンジャケット|ウ[イィ]ンドブレーカー|WINDBREAKER|アノラック|ANORAK|ナイロンパーカー|"
                          r"フリース|FLEECE|ボアジャケット")),
    # その他アウター: ジャンパー・ブルゾン / マウンテンパーカー / スタジャン / スカジャン / カバーオール
    ("outer", re.compile(r"ブルゾン|BLOUSON|ジャンパー|マウンテンパーカー|MOUNTAIN\s?PARKA|スタジャン|スタジアムジャンパー|"
                         r"スカジャン|スーベニアジャケット|カバーオール|COVERALL|ス[ウ]?[イィ]ングトップ|"
                         r"(?<![A-Z0-9])G-?9(?![0-9])|ハリントン|HARRINGTON")),
    # 上のどれでもない「ジャケット」（ワークジャケット・コーチジャケット・シャツジャケット 等）
    ("jacket", re.compile(r"ジャケット|JACKET")),
]


def outer_class(title: str) -> str | None:
    """タイトル → アウター4ジャンルのどれか（jacket/coat/down/outer）。アウターの語が無ければ None"""
    t = _NOT_OUTER_RE.sub(" ", unicodedata.normalize("NFKC", title or "").upper())
    for key, rx in _OUTER_RULES:
        if rx.search(t):
            return key
    return None


def mercari_category_ids(genre: dict, gender: str = "") -> list[int]:
    """ジャンルのメルカリのカテゴリID（メンズ/レディースを選んでいればその分だけ）"""
    m = genre.get("mercari") or {}
    if gender == "メンズ":
        return list(m.get("men") or [])
    if gender == "レディース":
        return list(m.get("women") or [])
    return list(m.get("men") or []) + list(m.get("women") or [])

# ──────────────────────────────────────────────────────────────────────────────
# ブランド名の表記ゆれ（英字だけのタイトルも同じブランドと判定するため）
# model_extractor.BRAND_ALIASES に無いブランドだけ、フリーリサーチの実行中に足す（ファイルは変えない）。
# EN_QUERY: 仕入れ先を英字のブランド名でも検索するときの表記（英字だけのタイトルの出品を拾うため）
# ──────────────────────────────────────────────────────────────────────────────
EXTRA_ALIASES: dict[str, list[str]] = {
    "ポールスミス": ["ポールスミス", "ポール・スミス", "ポール スミス", "PAUL SMITH", "PAULSMITH", "PAUL+SMITH"],
    "ドルチェ&ガッバーナ": ["ドルチェ&ガッバーナ", "ドルチェアンドガッバーナ", "ドルチェ＆ガッバーナ", "ドルガバ",
                        "DOLCE&GABBANA", "DOLCE & GABBANA", "DOLCE&GABBANA", "D&G"],
    "ジョルジオアルマーニ": ["ジョルジオアルマーニ", "ジョルジオ アルマーニ", "ジョルジオ・アルマーニ", "GIORGIO ARMANI"],
    "エンポリオアルマーニ": ["エンポリオアルマーニ", "エンポリオ アルマーニ", "EMPORIO ARMANI"],
    "アルマーニコレツィオーニ": ["アルマーニコレツィオーニ", "アルマーニ コレツィオーニ", "ARMANI COLLEZIONI"],
    "エルメス": ["エルメス", "HERMES", "HERMÈS"],
    "バラクータ": ["バラクータ", "BARACUTA"],
    "ブルックスブラザーズ": ["ブルックスブラザーズ", "ブルックス ブラザーズ", "BROOKS BROTHERS"],
    "ダンヒル": ["ダンヒル", "DUNHILL"],
    "ポロラルフローレン": ["ポロラルフローレン", "ポロ ラルフローレン", "POLO RALPH LAUREN", "ラルフローレン", "RALPH LAUREN"],
    "ラルフローレン": ["ラルフローレン", "ラルフ ローレン", "RALPH LAUREN"],
    "ジャンニヴェルサーチ": ["ジャンニヴェルサーチ", "ジャンニ ヴェルサーチ", "GIANNI VERSACE"],
    "ピンクハウス": ["ピンクハウス", "PINK HOUSE", "PINKHOUSE"],
    "ヴィヴィアンウエストウッド": ["ヴィヴィアンウエストウッド", "ヴィヴィアン ウエストウッド", "ヴィヴィアン", "VIVIENNE WESTWOOD"],
    "クロエ": ["クロエ", "CHLOE", "CHLOÉ"],
    "ランバン": ["ランバン", "LANVIN"],
    "ニナリッチ": ["ニナリッチ", "NINA RICCI"],
    "ヒステリックグラマー": ["ヒステリックグラマー", "HYSTERIC GLAMOUR"],
    "マッキントッシュ": ["マッキントッシュ", "MACKINTOSH"],
    "ヨウジヤマモト": ["ヨウジヤマモト", "ヨウジ ヤマモト", "YOHJI YAMAMOTO"],
    "コムデギャルソン": ["コムデギャルソン", "コム デ ギャルソン", "COMME DES GARCONS", "COMME des GARÇONS"],
    "ディースクエアード": ["ディースクエアード", "DSQUARED", "DSQUARED2"],
    "ディーゼル": ["ディーゼル", "DIESEL"],
    "アレキサンダーワン": ["アレキサンダーワン", "アレキサンダー ワン", "ALEXANDER WANG"],
    "ハリスツイード": ["ハリスツイード", "HARRIS TWEED"],
    "ボッテガヴェネタ": ["ボッテガヴェネタ", "ボッテガ ヴェネタ", "ボッテガ", "BOTTEGA VENETA"],
    "ロベルタディカメリーノ": ["ロベルタディカメリーノ", "ロベルタ", "ROBERTA DI CAMERINO"],
    "ドゥロワー": ["ドゥロワー", "DRAWER"],
    "セオリーリュクス": ["セオリーリュクス", "THEORY LUXE"],
    "レリアン": ["レリアン", "LEILIAN"],
    "ヘルノ": ["ヘルノ", "HERNO"],
    "マッキントッシュロンドン": ["マッキントッシュロンドン", "MACKINTOSH LONDON"],
    "サルヴァトーレフェラガモ": ["フェラガモ", "FERRAGAMO"],
    "フェラガモ": ["フェラガモ", "FERRAGAMO"],
    "ロロピアーナ": ["ロロピアーナ", "LORO PIANA"],
    "ブリオーニ": ["ブリオーニ", "BRIONI"],
    "エルメネジルドゼニア": ["エルメネジルドゼニア", "ゼニア", "ZEGNA"],
    "マックスマーラ": ["マックスマーラ", "MAX MARA", "MAXMARA"],
    "フォクシー": ["フォクシー", "FOXEY"],
    "ステラマッカートニー": ["ステラマッカートニー", "STELLA MCCARTNEY"],
    "マルニ": ["マルニ", "MARNI"],
    "ジルサンダー": ["ジルサンダー", "JIL SANDER"],
    "サンローラン": ["サンローラン", "SAINT LAURENT", "YSL", "イヴサンローラン"],
    "ジバンシィ": ["ジバンシィ", "ジバンシー", "GIVENCHY"],
    "フェンディ": ["フェンディ", "FENDI"],
    "ヴァレンティノ": ["ヴァレンティノ", "バレンティノ", "VALENTINO"],
    "トムブラウン": ["トムブラウン", "THOM BROWNE"],
    "メゾンマルジェラ": ["メゾンマルジェラ", "マルジェラ", "MARGIELA"],
    "ストーンアイランド": ["ストーンアイランド", "STONE ISLAND"],
    "ラルディーニ": ["ラルディーニ", "LARDINI"],
    "タリアトーレ": ["タリアトーレ", "TAGLIATORE"],
    "ボリオリ": ["ボリオリ", "BOGLIOLI"],
    "ベルヴェスト": ["ベルヴェスト", "BELVEST"],
}
# 仕入れ先を英字名でも検索するブランド（英字だけの出品タイトルが多い）。値＝検索に使う英字名
EN_QUERY: dict[str, str] = {
    "ポールスミス": "PAUL SMITH", "ドルチェ&ガッバーナ": "DOLCE&GABBANA", "ジョルジオアルマーニ": "GIORGIO ARMANI",
    "エンポリオアルマーニ": "EMPORIO ARMANI", "バーバリー": "BURBERRY", "グッチ": "GUCCI", "エルメス": "HERMES",
    "クリスチャンディオール": "DIOR", "タトラス": "TATRAS", "モンクレール": "MONCLER", "カナダグース": "CANADA GOOSE",
    "ピレネックス": "PYRENEX", "バラクータ": "BARACUTA", "ブルックスブラザーズ": "BROOKS BROTHERS",
    "ダンヒル": "DUNHILL", "ヴィヴィアンウエストウッド": "VIVIENNE WESTWOOD", "プラダ": "PRADA",
    "セリーヌ": "CELINE", "ルイヴィトン": "LOUIS VUITTON", "シャネル": "CHANEL", "ヘルノ": "HERNO",
    "デュベティカ": "DUVETICA", "ノースフェイス": "NORTH FACE",
}

# ──────────────────────────────────────────────────────────────────────────────
# メンズ / レディース の判定
# ──────────────────────────────────────────────────────────────────────────────
_MEN_RE = re.compile(r"メンズ|MEN'?S|(?<![A-Z])MENS(?![A-Z])|紳士|男性用|男性|ムッシュ|(?<![A-Z])HOMME(?![A-Z])|(?<![A-Z])UOMO(?![A-Z])|"
                     r"ウエストウッドマン|WESTWOOD\s*MAN(?![A-Z])")
_WOMEN_RE = re.compile(r"レディース|レディス|(?<![A-Z])LADIES|LADY'?S|WOMEN'?S|(?<![A-Z])WOMENS|ウィメンズ|ウイメンズ|婦人|女性用|女性|"
                       r"(?<![A-Z])FEMME(?![A-Z])|(?<![A-Z])DONNA(?![A-Z])|ウーマン|(?<![A-Z])WOMAN(?![A-Z])|"
                       r"(?<!\d)(?:3|5|7|9|11|13|15|17|19)\s*号|ミセス")
# アイテムの種類から分かるもの（弱い手がかり。タイトルの性別の表記がないときだけ使う）
_WOMEN_ITEM_RE = re.compile(r"スカート|SKIRT|ワンピース|ブラウス|パンプス|ミュール|キュロット|ドレス|ノーカラー|ツインニット|"
                            r"セレモニー|入学式|卒業式|入園|卒園")
# 日本の紳士服のサイズ表記（A4・AB5・YA6・BB7 等。セットアップ・ジャケット・コートのタイトルだけ）
_MEN_SIZE_RE = re.compile(r"(?<![A-Z0-9])(?:YA|AB|BB|BE|A|Y|B|E)\s?[2-9](?![0-9A-Z号])")
_UNISEX_RE = re.compile(r"ユニセックス|UNISEX|男女兼用|男女共用|兼用")
# タトラスの品番の先頭: M=メンズ / L=レディース（MTA・MTK・MTAT・MTLA / LTA・LTK・LTAT 等）
_TATRAS_GENDER_RE = re.compile(r"(?<![A-Z])([ML])T(?:A|K|AT|LA|KE|NU|SE)?[\s\-]?\d")

GENDERS = ("メンズ", "レディース")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").upper()


def detect_gender(title: str, description: str = "", brand: str = "", genre_key: str = "") -> str:
    """出品のメンズ/レディースを判定する。"メンズ" / "レディース" / "男女兼用" / "不明"。
    タイトル（強い手がかり）→ 説明文 → タトラスの品番の先頭文字 → アイテムの種類・紳士服のサイズ表記（弱い手がかり）の順"""
    t = _norm(title)
    d = _norm(description)
    for text in (t, d):
        if not text:
            continue
        men, women = bool(_MEN_RE.search(text)), bool(_WOMEN_RE.search(text))
        if men and not women:
            return "メンズ"
        if women and not men:
            return "レディース"
        if _UNISEX_RE.search(text):
            return "男女兼用"
        if men and women:
            # 「メンズ レディース」両方の表記（兼用・説明文の定型文）→ 次の手がかりへ
            continue
    if brand == "タトラス":
        for text in (t, d):
            m = _TATRAS_GENDER_RE.search(text)
            if m:
                return "メンズ" if m.group(1) == "M" else "レディース"
    if _WOMEN_ITEM_RE.search(t):
        return "レディース"
    if genre_key in ("setup", "jacket", "coat", "outer") and _MEN_SIZE_RE.search(t):
        return "メンズ"
    return "不明"


def gender_ok(detected: str, wanted: str) -> bool:
    """選んだ性別（wanted: メンズ/レディース/指定なし）で残すか。不明・男女兼用は残す（表示で「不明」と分かるようにする）"""
    if wanted not in GENDERS:
        return True
    return detected not in GENDERS or detected == wanted


# ──────────────────────────────────────────────────────────────────────────────
# 除外（キッズ・付属品だけ・偽物 等）
# ──────────────────────────────────────────────────────────────────────────────
_JUNK_RE = re.compile(
    r"キッズ|(?<![A-Z])KIDS|子供|子ども|こども|ジュニア|JUNIOR|ベビー|(?<![A-Z])BABY|ボーイズ|ガールズ|"
    r"(?<!\d)(?:80|90|95|100|110|120|130|140|150)\s*(?:CM|センチ)(?!.*(?:着丈|身幅|肩幅|袖丈|総丈|股下|ウエスト))|"
    r"偽物|偽造|レプリカ|コピー品|スーパーコピー|ノベルティ|付録|ハンガーのみ|ショッパー|紙袋|空箱|箱のみ|タグのみ|"
    r"ボタンのみ|替えボタン|予備ボタン|パーツのみ|生地のみ|ハギレ|はぎれ|カタログ|ステッカー|ぬいぐるみ|"
    r"まとめ売り|まとめ|セット売り|福袋|\d+\s*点セット|\d+\s*着セット|\d+\s*点まとめ|"
    r"写真のみ|画像のみ|サンプル品|トルソー|マネキン"
)


def _brand_like_re(brand: str) -> re.Pattern:
    """「ポールスミス風」「グッチ似」などの類似品"""
    names = "|".join(re.escape(_norm(a)) for a in model_extractor.brand_aliases(brand))
    return re.compile(r"(?:" + names + r")\s*(?:風|似|タイプ|系|っぽ|好き|好きな方)")


# ──────────────────────────────────────────────────────────────────────────────
# 注目ポイント（利益商品共有 seed.json の特徴タグのうち、タイトル・説明文から読めるもの）
# ──────────────────────────────────────────────────────────────────────────────
FEATURES: list[tuple[str, str]] = [
    ("XLサイズ以上", r"(?<![A-Z0-9])(?:XL|XXL|XXXL|2XL|3XL|4XL|LL|3L|4L|5L)(?![A-Z0-9])|大きいサイズ|ビッグサイズ|"
                    r"(?:サイズ|SIZE)\s*(?:5[2-8]|60)(?!\d)|(?<![A-Z0-9])(?:AB|BB|BE|E|B)\s?[7-9](?![0-9])"),
    ("派手裏地", r"(?:総柄|花柄|ペイズリー|ストライプ|マルチ|プリント|派手|フラワー|レオパード|チェック|柄)\s*(?:の)?\s*裏地|"
                r"裏地\s*(?:が|は)?\s*(?:総柄|花柄|ペイズリー|ストライプ|マルチ|柄|派手|フラワー|レオパード)|派手裏|裏地\s*(?:デザイン|アート)"),
    ("ダブルボタン", r"ダブル\s*(?:ボタン|ブレスト|ブレステッド|の?ジャケット|の?スーツ|の?コート|合わせ)|"
                    r"(?<![0-9A-Z])[4-8]B(?![A-Z0-9])|DOUBLE[\s\-]*BREAST"),
    ("少ないボタン数", r"(?<![0-9A-Z])[12]B(?![A-Z0-9])|[12]つボタン|(?<![0-9])[12]ボタン"),
    ("スリーピース", r"スリーピース|3ピース|三つ揃|(?<![0-9A-Z])3P(?![A-Z0-9])|ベスト付き|ジレ付き"),
    ("パンツスーツ", r"パンツスーツ"),
    ("カシミヤ", r"カシミ[ヤア]|CASHMERE"),
    ("シルク", r"シルク|SILK"),
    ("モヘヤ", r"モヘ[アヤ]|MOHAIR"),
    ("アンゴラ", r"アンゴラ|ANGORA"),
    ("ツイード", r"ツイード|TWEED"),
    ("麻素材", r"リネン|(?<![一-鿿])麻(?![一-鿿])|LINEN"),
    ("ベロア", r"ベロア|ベルベット|VELOUR|VELVET"),
    ("高級生地（ゼニア等）", r"ゼニア|ZEGNA|ロロピアーナ|LORO\s*PIANA|カノニコ|CANONICO|(?<![A-Z])REDA(?![A-Z])|"
                          r"ドーメル|DORMEUIL|スキャバル|SCABAL|ヴィターレ\s*バルベリス|VBC"),
    ("ラムレザー", r"ラム\s*レザー|ラムスキン|羊革|LAMB\s*(?:LEATHER|SKIN)"),
    ("ムートン", r"ムートン|MOUTON|シアリング|SHEARLING"),
    ("ファー付き", r"ファー|(?<![A-Z])FUR(?![A-Z])"),
    ("ライナー付き", r"ライナー"),
    ("ノバチェック", r"ノ[バヴ]\s*チェック|NOVA\s*CHECK"),
    ("メガチェック", r"メガチェック"),
    ("マルチストライプ", r"マルチストライプ"),
    ("派手柄", r"総柄|派手柄|花柄|ペイズリー|レオパード|ヒョウ柄|豹柄|ゼブラ|迷彩|カモフラ|アニマル柄"),
    ("ロング丈", r"ロング|(?<![A-Z])LONG(?![A-Z])"),
    ("金ボタン", r"金ボタン|ゴールドボタン|金釦"),
    ("銀ボタン", r"銀ボタン|シルバーボタン|銀釦"),
    ("ロゴボタン", r"ロゴボタン|ロゴ釦|刻印ボタン"),
    ("くるみボタン", r"くるみボタン|包みボタン"),
    ("ヴィンテージ・旧タグ", r"ヴィンテージ|ビンテージ|VINTAGE|旧タグ|オールド|(?<![A-Z])OLD(?![A-Z])"),
    ("新しいタグ", r"現行タグ|新タグ|現行品"),
    ("未使用・タグ付き", r"未使用|タグ付き|タグ有り|新品"),
    ("リバーシブル", r"リバーシブル|REVERSIBLE"),
    ("ワッペン・刺繍", r"ワッペン|刺繍|エンブロイダリー|EMBROIDER"),
    ("細身シルエット", r"細身|スリム|(?<![A-Z])SLIM"),
    ("英国製", r"英国製|イギリス製|MADE\s*IN\s*(?:ENGLAND|UK|U\.K\.|GREAT\s*BRITAIN)"),
    ("イタリア製", r"イタリア製|MADE\s*IN\s*ITALY"),
    ("ピークドラペル", r"ピークド?\s*ラペル|PEAK(?:ED)?\s*LAPEL"),
    ("ヘリンボーン", r"ヘリンボーン|HERRINGBONE"),
    ("付属品完備", r"付属品\s*(?:完備|あり|有り|付き)|箱付き|保存袋付き|ギャランティ(?:カード)?\s*付|替えボタン付"),
    # ブランドのライン（売り切れの比較で高い/安いが分かるように個別のタグにする）
    ("PSライン", r"(?<![A-Z])PS\s*(?:PAUL|ポール)|ピーエス\s*ポール"),
    ("LONDONライン", r"(?:ポール\s*スミス|PAUL\s*SMITH)\s*(?:LONDON|ロンドン)"),
    ("コレクションライン", r"(?:ポール\s*スミス|PAUL\s*SMITH)\s*(?:COLLECTION|コレクション)"),
    ("ブラックレーベル", r"ブラック\s*レーベル|BLACK\s*LABEL|(?:ポール\s*スミス|PAUL\s*SMITH)\s*(?:BLACK|ブラック)"),
    ("ブルーレーベル", r"ブルー\s*レーベル|BLUE\s*LABEL"),
    ("ベルト付き", r"ベルト付"),
]
_FEATURE_RES = [(tag, re.compile(rx)) for tag, rx in FEATURES]
# 説明文では見ない特徴（店の定型文「新品・未使用品は…」「ロング丈の商品は…」「付属品：なし」等に反応するため）
_TITLE_ONLY_FEATURES = {"未使用・タグ付き", "付属品完備", "ロング丈", "ヴィンテージ・旧タグ", "新しいタグ", "細身シルエット",
                        "ライナー付き", "ファー付き", "XLサイズ以上", "PSライン", "LONDONライン", "コレクションライン",
                        "ブラックレーベル", "ブルーレーベル", "少ないボタン数"}


def detect_features(text: str) -> list[str]:
    t = _norm(text)
    return [tag for tag, rx in _FEATURE_RES if rx.search(t)]


def detect_item_features(title: str, description: str = "") -> list[dict]:
    """出品の注目ポイント [{tag, where: タイトル/説明文}]。説明文は定型文に反応しやすい特徴を除いて見る"""
    out = [{"tag": t, "where": "タイトル"} for t in detect_features(title)]
    have = {f["tag"] for f in out}
    if description:
        for t in detect_features(description):
            if t not in have and t not in _TITLE_ONLY_FEATURES:
                out.append({"tag": t, "where": "説明文"})
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 候補（ブランド・ジャンル）: 登録済みのブランド + 利益商品共有（seed.json）
# ──────────────────────────────────────────────────────────────────────────────

def _load_seed() -> list[dict]:
    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # 重複投稿（duplicateOf あり）は数えない。投稿者名などの個人の情報は使わない
        return [r for r in data if isinstance(r, dict) and not r.get("duplicateOf")]
    except Exception as e:
        print(f"[フリーリサーチ] 利益商品共有のデータを読めませんでした（候補は登録済みのブランドだけ）: {e}")
        return []


def suggestions(registered_brands: list[str]) -> dict:
    """画面の入力候補。brands: [{name, count, avg_profit, genres}]（利益商品共有の件数順→平均利益順。登録済みのブランドも含む）、
    genres: [{key, label, icon, count}]、features: [{tag, count}]"""
    seed = _load_seed()
    agg: dict[str, dict] = {}
    for r in seed:
        b = (r.get("brand") or "").strip()
        if not b:
            continue
        a = agg.setdefault(b, {"name": b, "count": 0, "profit_sum": 0, "genres": {}})
        a["count"] += 1
        a["profit_sum"] += int(r.get("profit") or 0)
        gk = _seed_genre_key(r.get("genreMajor"))
        if gk:
            a["genres"][gk] = a["genres"].get(gk, 0) + 1
    brands = []
    for a in agg.values():
        brands.append({"name": a["name"], "count": a["count"],
                       "avg_profit": round(a["profit_sum"] / a["count"]) if a["count"] else 0,
                       "genres": dict(sorted(a["genres"].items(), key=lambda kv: -kv[1])),
                       "registered": a["name"] in registered_brands})
    brands.sort(key=lambda b: (-b["count"], -b["avg_profit"]))
    for b in registered_brands:
        if b not in agg:
            brands.append({"name": b, "count": 0, "avg_profit": 0, "genres": {}, "registered": True})
    genre_counts: dict[str, int] = {}
    for r in seed:
        gk = _seed_genre_key(r.get("genreMajor"))
        if gk:
            genre_counts[gk] = genre_counts.get(gk, 0) + 1
    genres = [{"key": g["key"], "label": g["label"], "icon": g["icon"], "count": genre_counts.get(g["key"], 0),
               "queries": len(g["queries"]), "hint": g.get("hint", "")} for g in GENRES]
    feat_counts: dict[str, int] = {}
    seed_tags = {t for r in seed for t in (r.get("features") or [])}
    for r in seed:
        for t in detect_features(" ".join([r.get("title") or "", " ".join(r.get("features") or [])])):
            feat_counts[t] = feat_counts.get(t, 0) + 1
    features = [{"tag": tag, "count": feat_counts.get(tag, 0), "in_seed": tag in seed_tags} for tag, _ in FEATURES]
    features.sort(key=lambda f: -f["count"])
    return {"brands": brands, "genres": genres, "features": features, "seed_count": len(seed),
            # 仕入れ先を英字名でも検索するブランド（画面の所要時間の目安用）
            "en_brands": sorted(EN_QUERY)}


def _seed_genre_key(major: str | None) -> str | None:
    for g in GENRES:
        if g["seed"] == major:
            return g["key"]
    return None


def ensure_aliases(brand: str) -> None:
    """model_extractor に無いブランドの表記ゆれを、この実行中だけ足す（英字だけのタイトルも同じブランドと判定する）"""
    if brand in model_extractor.BRAND_ALIASES:
        return
    if brand in EXTRA_ALIASES:
        model_extractor.BRAND_ALIASES[brand] = EXTRA_ALIASES[brand]


def brand_queries(brand: str) -> list[str]:
    """仕入れ先の検索に使うブランド名（カタカナ + 英字名があれば英字名も）"""
    out = [brand]
    en = EN_QUERY.get(brand)
    if en and en not in out:
        out.append(en)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 相場（メルカリの売り切れ）
# ──────────────────────────────────────────────────────────────────────────────
_CACHE_TABLE = "free_market_cache"


def _cache_init() -> None:
    with database._conn() as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {_CACHE_TABLE} (
            keyword TEXT PRIMARY KEY, items_json TEXT NOT NULL, fetched_at TEXT NOT NULL)""")


def _cache_get(keyword: str, max_age_hours: float) -> list[dict] | None:
    with database._conn() as conn:
        row = conn.execute(f"SELECT items_json, fetched_at FROM {_CACHE_TABLE} WHERE keyword = ?",
                           (keyword,)).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["fetched_at"]) < datetime.now() - timedelta(hours=max_age_hours):
            return None
        return json.loads(row["items_json"])
    except Exception:
        return None


def _cache_put(keyword: str, items: list[dict]) -> None:
    with database._conn() as conn:
        conn.execute(f"INSERT OR REPLACE INTO {_CACHE_TABLE} (keyword, items_json, fetched_at) VALUES (?, ?, ?)",
                     (keyword, json.dumps(items, ensure_ascii=False), datetime.now().isoformat()))


stats = {"sold_fetch": 0, "sold_cached": 0, "desc_fetched": 0, "desc_cached": 0,
         "model_upgraded": 0, "ocr_upgraded": 0, "market_none": 0}
_memo: dict[str, list[dict]] = {}


def _sold_items(page, keyword: str, count: int, cache_hours: float,
                category_ids: list[int] | None = None) -> list[dict]:
    """メルカリの売り切れ（ログインなし）。タイトルにブランド名（キーワードの先頭語）がある出品だけ（mercari 側の確認）。
    category_ids: メルカリのカテゴリで絞る（検索URLの category_id。ログインなしで使える）。
    取得結果（価格・タイトル・URL）は DB に cache_hours 時間保存し、ジャンル・性別・特徴の絞り込みはこちらで毎回やり直す"""
    cat = ",".join(str(c) for c in category_ids) if category_ids else ""
    key = f"{keyword}|{count}" + (f"|cat={cat}" if cat else "")
    if key in _memo:
        return _memo[key]
    cached = _cache_get(key, cache_hours)
    if cached is not None:
        stats["sold_cached"] += 1
        print(f"  [キャッシュ] メルカリ売り切れ「{keyword}」 {len(cached)}件")
        _memo[key] = cached
        return cached
    from scrapers import mercari as mercari_scraper
    print(f"  [Mercari] 「{keyword}」の売り切れを取得中..." + ("（メルカリのカテゴリで絞り込み）" if cat else ""))
    stats["sold_fetch"] += 1
    got = mercari_scraper.get_sold_items(page, keyword, count=count, category_ids=category_ids)
    items = [{"price": r["price"], "title": r.get("title", ""), "url": r.get("url", "")} for r in got]
    print(f"    → {len(items)}件")
    if items:
        _cache_put(key, items)
    _memo[key] = items
    return items


def _quartiles(prices: list[int]) -> tuple[int, int, int]:
    p = sorted(prices)
    if len(p) < 4:
        med = int(statistics.median(p))
        return p[0], med, p[-1]
    q = statistics.quantiles(p, n=4, method="inclusive")
    return int(q[0]), int(q[1]), int(q[2])


def summarize(prices: list[int]) -> dict | None:
    """外れ値を除いた中央値・四分位・信頼度。売り切れが3件未満なら None"""
    n_all = len(prices)
    if n_all < 3:
        return None
    p = sorted(prices)
    cut = n_all // 10 if n_all >= 10 else 0           # 上下10%（偽物・まとめ売り・別アイテムの混入を除く）
    trimmed = p[cut:n_all - cut] if cut else p
    q1, med, q3 = _quartiles(trimmed)
    spread = (q3 - q1) / med if med else 9.9
    if n_all >= 20 and spread <= 0.6:
        conf = "高"
    elif n_all >= 8 and spread <= 1.0:
        conf = "中"
    else:
        conf = "低"
    return {"n": n_all, "used": len(trimmed), "median": med, "q1": q1, "q3": q3,
            "min": trimmed[0], "max": trimmed[-1], "spread": round(spread, 2), "confidence": conf}


class GenreMarket:
    """ブランド × ジャンル（× 性別）の売り切れ。features の比較にも使う"""

    def __init__(self, brand: str, genre: dict, gender: str, sold: list[dict], queries: list[str]):
        self.brand, self.genre, self.gender = brand, genre, gender
        self.sold = sold
        self.queries = queries
        self.category_ids: list[int] = []   # メルカリのカテゴリで絞ったときのID（画面の「メルカリで見る」にも付ける）
        self.summary = summarize([s["price"] for s in sold])
        self._feat_cache: dict[str, dict | None] = {}

    def feature_compare(self, tag: str) -> dict | None:
        """売り切れの中で、特徴 tag がタイトルにあるもの/ないものの中央値（両方3件以上のときだけ）"""
        if tag in self._feat_cache:
            return self._feat_cache[tag]
        rx = dict(_FEATURE_RES)[tag]
        with_p = [s["price"] for s in self.sold if rx.search(_norm(s["title"]))]
        without = [s["price"] for s in self.sold if not rx.search(_norm(s["title"]))]
        out = None
        if len(with_p) >= 3 and len(without) >= 3:
            mw, mo = int(statistics.median(with_p)), int(statistics.median(without))
            out = {"with_median": mw, "with_n": len(with_p), "without_median": mo, "without_n": len(without),
                   "diff_pct": round((mw - mo) / mo * 100) if mo else 0}
        elif with_p:
            out = {"with_median": int(statistics.median(with_p)), "with_n": len(with_p),
                   "without_median": None, "without_n": len(without), "diff_pct": None}
        self._feat_cache[tag] = out
        return out


def _genre_re(genre: dict) -> tuple[re.Pattern, re.Pattern | None]:
    m = re.compile(genre["match"])
    c = re.compile(genre["conflict"]) if genre.get("conflict") else None
    return m, c


# セットアップ・スーツ: 「スーツ ベスト」「スーツ用ジレ」のような単品（ジャケット・パンツ等の上下の語が無い）を除く
_SETUP_PIECE_RE = re.compile(r"ベスト|ジレ|GILET|VEST")
_SETUP_SET_RE = re.compile(r"上下|セットアップ|SET\s?UP|スリーピース|3ピース|ツーピース|2ピース|3P|ジャケット|パンツ|スラックス|スカート|"
                           r"スーツ\s*(?:上下|一式)")

# ── 服のジャンルで「タイトルに服の語があるが本体は小物」を除く（2026-11-26 追加） ──
# 例: 「ルイヴィトン エピ サンチュール ベルト レザー … ビジネス スーツ メンズ」「クラヴァット … Yシャツ スーツ … ネクタイ」
# （「ビジネス スーツ」等は検索対策で足された語。本体はベルト・ネクタイ）。
# 小物の語（下の _ACC_CLASSES）がタイトルにあり、付属を示す表現（〜付き/付属/セット/欠品 等・柄/プリント）でないとき:
#   - その語がジャンルの服の語（スーツ・ジャケット 等）より前にある → 本体は小物とみなして除く
#     （出品タイトルは本体の名前を先に書く。「スーツ 上下 ネクタイ」のように服の後ろの小物は同梱とみなす）
#   - タイトルに服の語が無い（型番・モデル名の検索 model_genre）→ 除く
#   - セットアップ・スーツで、上下を示す語（上下/セットアップ/ジャケット/パンツ 等 = _SETUP_SET_RE）が無い → 位置に関係なく除く
#     （「スーツ」1語だけなら、ほぼ「ビジネス スーツ」等の用途の語）
_ACC_CLASSES: dict[str, str] = {
    "ベルト": r"(?<!ロ)ベルト|(?<![A-Z])BELTS?(?![A-Z])|サンチュール|CEINTURE",
    "ネクタイ": r"ネクタイ|NECKTIE|クラヴァット|クラバット|CRAVATE|ボウタイ|BOW\s?TIE|アスコットタイ|ループタイ|ニットタイ|パピヨン",
    "タイピン・カフス": r"タイピン|タイバー|タイクリップ|タイタック|TIE\s?(?:PIN|BAR|CLIP)|カフス|カフリンクス|CUFF\s?LINKS?",
    "財布・革小物": r"財布|ウォレット|(?<![A-Z])WALLET|コインケース|カードケース|キーケース|名刺入れ|マネークリップ|パスケース",
    "バッグ": r"バッグ|(?<![A-Z])BAGS?(?![A-Z])|リュック|バックパック|ブリーフケース|クラッチ|ポーチ",
    "靴": r"靴(?!下)|シューズ|(?<![A-Z])SHOES|スニーカー|SNEAKERS?|ブーツ(?!カット)|(?<![A-Z])BOOTS(?![A-Z])|ローファー|"
          r"LOAFERS?|パンプス|サンダル|モカシン",
    "アクセサリー": r"ネックレス|ブレスレット|バングル|ペンダント|ピアス|イヤリング|腕時計|キーホルダー|キーリング",
    "マフラー・帽子等": r"マフラー|ストール|手袋|グローブ(?!レザー)|帽子|ハット|キャップ(?!スリーブ)",
    "シャツ単品": r"(?<![A-Z])Yシャツ|ワイシャツ|ドレスシャツ|カッターシャツ",
}
_ACC_COMMON = ("ベルト", "ネクタイ", "タイピン・カフス", "財布・革小物", "バッグ", "靴", "アクセサリー", "マフラー・帽子等")
# ジャンル → 除く小物の種類（セットアップ・スーツだけシャツ単品も。トップスはシャツが本体のことがあるので入れない）
_ACC_FOR: dict[str, tuple[str, ...]] = {
    "setup": _ACC_COMMON + ("シャツ単品",),
    "jacket": _ACC_COMMON, "coat": _ACC_COMMON, "down": _ACC_COMMON, "outer": _ACC_COMMON,
    "tops": _ACC_COMMON, "bottoms": _ACC_COMMON, "onepiece": _ACC_COMMON,
}
_ACC_RES = {k: re.compile(v) for k, v in _ACC_CLASSES.items()}
# 付属・服の一部・柄を示す（小物の語の直後）
_ACC_ATTACHED_AFTER = re.compile(r"\s*(?:付|附|つき|ツキ|セット|同梱|おまけ|オマケ|ループ|通し|欠品|なし|無し|紛失|あり|有り|有|"
                                 r"柄|プリント|モチーフ|刺繍|PRINT|ポケット|穴|跡|部分)")
# ネクタイの語の直後が服 → 服のデザイン（ボウタイブラウス・「ボウタイ シャツワンピース」・ボウタイカラー）
_TIE_DESIGN_AFTER = re.compile(r"\s*(?:ブラウス|シャツ|ワンピ|カラー|襟|デザイン)")
# 服の一部を示す（小物の語の直前）。「＋ネクタイ」「&ベルト」も同梱
_ACC_ATTACHED_BEFORE = re.compile(r"(?:ウエスト|ウェスト|共布|共地|共生地|同素材|袖|裾|フード|[+＋&＆・]|と)\s*$")


def _garment_pos(t: str, genre_key: str) -> int | None:
    """タイトル（_norm 済み）でジャンルの服の語が最初に出る位置。無ければ None"""
    g = GENRE_BY_KEY.get(genre_key)
    if g is None:
        return None
    pos = [m.start() for m in [re.search(g["match"], t)] if m]
    if genre_key in OUTER_KEYS:
        tt = _NOT_OUTER_RE.sub(lambda m: " " * len(m.group()), t)
        pos += [m.start() for _, rx in _OUTER_RULES for m in [rx.search(tt)] if m]
    return min(pos) if pos else None


def accessory_item(title: str, genre_key: str) -> str | None:
    """服のジャンルで、本体が小物（ベルト・ネクタイ・財布・バッグ・靴 等）と思われるタイトルなら小物の種類、そうでなければ None"""
    kinds = _ACC_FOR.get(genre_key)
    if not kinds:
        return None
    t = _norm(title)
    gpos = _garment_pos(t, genre_key)
    loose_setup = genre_key == "setup" and not _SETUP_SET_RE.search(t)
    for kind in kinds:
        for m in _ACC_RES[kind].finditer(t):
            if _ACC_ATTACHED_AFTER.match(t, m.end()) or _ACC_ATTACHED_BEFORE.search(t[:m.start()]):
                continue
            if kind == "ネクタイ" and _TIE_DESIGN_AFTER.match(t, m.end()):
                continue
            if gpos is None or m.start() < gpos or loose_setup:
                return kind
    return None


# ── 逆向き: 小物のジャンル（バッグ・靴・ネクタイ・小物）で「本体は服」を除く（2026-11-26 追加） ──
# 服の語がジャンルの小物の語より前にある → 本体は服（「トレンチコート ベルト付き」「テーラードジャケット ショルダーパッド」）。
# 服の語が後ろだけ（「ベルト レザー … ビジネス スーツ」「ネクタイ シルク スーツに」）は検索対策の語とみなして残す。
# 小物の語のうち付属・柄（〜付き/付属/柄/プリント 等）と、服の一部の語（ブーツカット・ショルダーパッド 等）は小物の語に数えない。
_ITEM_GENRES = ("bag", "shoes", "tie", "komono")
_GARMENT_RE = re.compile(
    r"スーツ(?!ケース|カバー|用|に|向け)|(?<![A-Z])SUIT(?!CASE|\s?CASE)|セットアップ|上下|ジャケット|JACKET|ブルゾン|BLOUSON|"
    r"(?<!レイン)コート(?!紙|ィング)|(?<![A-Z])COAT(?![A-Z])|(?<!プライス)(?<!カウント)(?<!ボタン)ダウン(?!ロード)|ワンピース|"
    r"ドレス(?!シューズ|シャツ)|スカート|パンツ|PANTS|スラックス|ジーンズ|シャツ|SHIRT|ブラウス|ニット(?!タイ|帽|キャップ)|"
    r"セーター|カーディガン|パーカー|トレーナー|ベスト|ジレ")
_ITEM_MASK_RE = re.compile(r"ブーツカット|ショルダーパッド|ショルダーライン|(?:ドロップ|オフ|ワン)ショルダー(?!\s*(?:バッグ|BAG))")
_ITEM_ATTACHED_AFTER = re.compile(r"\s*(?:付|附|つき|ツキ|柄|プリント|モチーフ|刺繍|PRINT)")


def garment_item(title: str, genre_key: str) -> bool:
    """小物のジャンルで、本体が服と思われるタイトルなら True"""
    if genre_key not in _ITEM_GENRES:
        return False
    t = _norm(title)
    rx = re.compile(GENRE_BY_KEY[genre_key]["match"])
    # 服の語のすぐ後ろに小物の語が続くもの（「ダウントートバッグ」「ニットタイ」「ドレスシューズ」）は素材・種類の語なので数えない
    g = next((m for m in _GARMENT_RE.finditer(t) if not rx.match(t, m.end())), None)
    if g is None:
        return False
    tt = _ITEM_MASK_RE.sub(lambda m: " " * len(m.group()), t)
    for m in rx.finditer(tt):
        if _ITEM_ATTACHED_AFTER.match(tt, m.end()):
            continue
        if genre_key == "tie" and _TIE_DESIGN_AFTER.match(tt, m.end()):
            return True     # 「ボウタイ シャツワンピース」= 服のデザイン
        return g.start() < m.start()
    return True     # 小物の語は付属・柄だけ（「ジャケット ストール付き」）


def title_fits(title: str, brand: str, genre: dict, like_re: re.Pattern, by_category: bool = False) -> str | None:
    """タイトルがブランド・ジャンルに合うか。合わなければ除く理由（ログ用）、合えば None。
    by_category: メルカリのカテゴリで絞った売り切れ（ジャンルはメルカリのカテゴリを信じ、タイトルのジャンルの語は見ない）"""
    t = _norm(title)
    if not model_extractor.has_brand(title, brand):
        return "ブランド名なし"
    if like_re.search(t):
        return "類似品（〜風）"
    m, c = _genre_re(genre)
    fn = genre.get("match_fn")
    key = genre.get("key")
    if by_category:
        # メルカリのカテゴリを信じる。ただしタイトルがはっきりコート・ダウンなのに別のアウターのカテゴリにある出品
        # （出品者のカテゴリ間違い。値段が大きく違う）は除く。ジャケットとその他アウターの境目はメルカリのカテゴリどおり
        oc = outer_class(title) if key in OUTER_KEYS else None
        if oc in ("coat", "down") and oc != key:
            return "別のアイテム"
    elif fn is not None:
        if not fn(title):
            return f"{genre['label']}の語なし"
        # 型番・モデル名の検索: ジャンルの語は要らないが、別のアウター（例: ジャケットを選んでコート）なら除く
        oc = outer_class(title) if key in OUTER_KEYS or key == "tops" else None
        if oc is not None and oc != key:
            return "別のアイテム"
    elif key in OUTER_KEYS:
        # アウター4ジャンルはメルカリの小分類の名前の付け方で1つだけに振り分ける（重ならない）
        oc = outer_class(title)
        if oc is None:
            return f"{genre['label']}の語なし"
        if oc != key:
            return "別のアイテム"
    else:
        if not m.search(t):
            return f"{genre['label']}の語なし"
        if key in ("tops", "bottoms", "onepiece") and outer_class(title) is not None:
            return "別のアイテム"
    if c is not None and c.search(t):
        return "別のアイテム"
    acc = accessory_item(title, key)
    if acc:
        return f"別のアイテム（{acc}）"
    if garment_item(title, key):
        return "別のアイテム（服）"
    if genre.get("key") == "setup" and _SETUP_PIECE_RE.search(t) and not _SETUP_SET_RE.search(t):
        return "上下でない（ベスト・ジャケット単品等）"
    if _JUNK_RE.search(t):
        return "キッズ・付属品・まとめ売り等"
    if _other_brand_count(title, brand) >= 2:
        return "他ブランド名の羅列"
    return None


def _other_brand_count(title: str, brand: str) -> int:
    """タイトルにある他のブランド名の数（「★ルイヴィトン★ディースクエアード★GUCCI」のような検索よけの羅列を見分ける）。
    自分のブランド名を含む表記（ポールスミス に対する ポールスミスブラック 等）は数えない"""
    own = [_norm(a) for a in model_extractor.brand_aliases(brand)]
    nt = _norm(title)
    n = 0
    for b, aliases in list(model_extractor.BRAND_ALIASES.items()) + list(EXTRA_ALIASES.items()):
        if b == brand:
            continue
        hit = False
        for a in aliases:
            na = _norm(a)
            if len(na) < 3 or any(na in o or o in na for o in own):
                continue
            if na in nt:
                hit = True
                break
        if hit:
            n += 1
    return n


def get_genre_market(page, brand: str, genre: dict, gender: str, settings: dict) -> GenreMarket:
    """メルカリの売り切れで、ブランド × ジャンル（× 性別）の相場。
    ジャンルにメルカリのカテゴリID（GENRES の mercari）があれば「ブランド名だけ」をそのカテゴリで絞って検索する
    （相場 = メルカリでそのカテゴリに出品された物。メンズ/レディースもカテゴリで分かれるので性別の語は付けない）。
    無ければ（自由入力のジャンル・型番等）従来どおり「ブランド + ジャンルの検索語（+ 性別）」で取り、タイトルで絞る"""
    like_re = _brand_like_re(brand)
    count = int(settings.get("free_sold_sample_count", 120))
    cache_hours = float(settings.get("price_cache_hours", 80))
    cats = mercari_category_ids(genre, gender) if settings.get("free_mercari_category_filter", True) else []
    if cats:
        queries = [brand]
    else:
        queries = [f"{brand} {w}" + (f" {gender}" if gender in GENDERS else "") for w in genre["queries"]]
    seen, sold = set(), []
    for q in queries:
        for s in _sold_items(page, q, count, cache_hours, category_ids=cats or None):
            k = s.get("url") or f"{s['title']}|{s['price']}"
            if k in seen:
                continue
            seen.add(k)
            if title_fits(s["title"], brand, genre, like_re, by_category=bool(cats)) is not None:
                continue
            if gender in GENDERS and not (cats and genre.get("mercari", {}).get("men" if gender == "メンズ" else "women")):
                g = detect_gender(s["title"], brand=brand, genre_key=genre["key"])
                if g in GENDERS and g != gender:
                    continue
            sold.append(s)
    gm = GenreMarket(brand, genre, gender, sold, queries)
    gm.category_ids = cats
    return gm


# ──────────────────────────────────────────────────────────────────────────────
# スキャン本体
# ──────────────────────────────────────────────────────────────────────────────

def parse_request(req: dict) -> dict:
    """画面の条件を整える。brands: 「、」「,」区切りの自由入力も受ける"""
    raw = req.get("free_brands") or []
    if isinstance(raw, str):
        raw = [raw]
    brands: list[str] = []
    for b in raw:
        for t in re.split(r"[,、，\n]+", str(b)):
            t = t.strip()
            if t and t not in brands:
                brands.append(t)
    genres = [GENRE_BY_KEY[k] for k in (req.get("free_genres") or []) if k in GENRE_BY_KEY]
    custom = [w.strip() for w in re.split(r"[,、，\n]+", req.get("free_custom_genre") or "") if w.strip()]
    for w in custom:
        genres.append(custom_genre(w))

    def _int(v, d):
        try:
            return int(float(v)) if v not in (None, "") else d
        except (TypeError, ValueError):
            return d
    pmin = max(0, _int(req.get("free_price_min"), 0))
    pmax = _int(req.get("free_price_max"), 30000)
    if pmax <= 0:
        pmax = 1_000_000
    if pmin > pmax:
        pmin, pmax = pmax, pmin
    gender = req.get("free_gender") if req.get("free_gender") in GENDERS else "指定なし"
    # 調べる組（ブランド, ジャンル, この組だけの仕入れ値の範囲・最低利益）。
    # list_pairs（仕入れリスト my_list.py から）があればその組だけ。無ければ ブランド × ジャンル の全組
    pairs: list[dict] = []
    list_pairs = req.get("list_pairs")
    if list_pairs:
        brands, genres = [], []
        for lp in list_pairs:
            b = str(lp.get("brand") or "").strip()
            if not b:
                continue
            lo = _int(lp.get("price_min"), None)
            hi = _int(lp.get("price_max"), None)
            if lo is not None and hi is not None and hi > 0 and lo > hi:
                lo, hi = hi, lo
            per = _int(lp.get("per_source"), None)
            per = max(1, min(per, 100)) if per else None
            terms = [str(x).strip() for x in (lp.get("terms") or []) if str(x).strip()]
            if terms:
                # 型番・モデル名を登録したブランド: 「ブランド + 型番/モデル名」で1つずつ調べ、タイトルにそれがある出品だけ
                toks = list(lp.get("genres") or [])
                base = genre_from_token(toks[0]) if len(toks) == 1 else None
                gens = [model_genre(b, t, base) for t in dict.fromkeys(terms)]
            else:
                gens = [g for g in (genre_from_token(tok) for tok in lp.get("genres") or []) if g is not None]
            for g in gens:
                pairs.append({"brand": b, "genre": g, "price_min": lo, "price_max": hi if hi else None,
                              "min_profit": _int(lp.get("min_profit"), None), "per_source": per})
                if b not in brands:
                    brands.append(b)
                if all(x["key"] != g["key"] for x in genres):
                    genres.append(g)
    else:
        pairs = [{"brand": b, "genre": g, "price_min": None, "price_max": None, "min_profit": None}
                 for b in brands for g in genres]
    return {"brands": brands, "genres": genres, "price_min": pmin, "price_max": pmax, "gender": gender,
            "pairs": pairs}


def custom_genre(w: str) -> dict:
    """自由入力のジャンル（検索語 = その語。タイトルにその語があるものだけ）"""
    w = w.strip()
    return {"key": f"custom:{w}", "label": w, "icon": "🏷️", "queries": [w],
            "match": re.escape(_norm(w)), "conflict": None, "seed": None}


def _term_key(s: str) -> str:
    """型番・モデル名の照合用（全角半角・大文字小文字・空白・ハイフン・中黒・長音の違いを無視）"""
    return re.sub(r"[\s\-_・･ー‐－]", "", unicodedata.normalize("NFKC", s or "").upper())


def model_genre(brand: str, term: str, base: dict | None = None) -> dict:
    """マイブランドで登録した型番・モデル名1つを「ジャンル」の形にする（検索語 = その語。タイトルにその語
    〔登録済みのモデル名なら英字名・表記ゆれも〕がある出品だけ）。base: ブランドに1つだけ選んだジャンル
    （別アイテムの除外・性別の判定・型番の相場の切り替えに使う）"""
    term = unicodedata.normalize("NFKC", term).strip()
    forms = {term}
    try:
        forms.update(model_extractor.model_aliases(term, brand))
    except Exception:
        pass
    keys = sorted({_term_key(f) for f in forms if _term_key(f)}, key=len, reverse=True)

    def fn(title: str, _keys=keys) -> bool:
        t = _term_key(title)
        return any(k in t for k in _keys)
    return {"key": base["key"] if base else f"model:{term}", "label": term,
            "icon": base["icon"] if base else "🔖", "queries": [term],
            "match": re.escape(_norm(term)), "match_fn": fn,
            "conflict": base.get("conflict") if base else None, "seed": None,
            "category_label": base["label"] if base else "", "term": term}


def genre_from_token(tok) -> dict | None:
    """ジャンルの指定（GENRES の key・表示名・"custom:語"・自由な語）→ ジャンルの dict"""
    t = str(tok or "").strip()
    if not t:
        return None
    if t in GENRE_BY_KEY:
        return GENRE_BY_KEY[t]
    for g in GENRES:
        if g["label"] == t:
            return g
    if t.startswith("custom:"):
        t = t[len("custom:"):].strip()
    return custom_genre(t) if t else None


def plan_size(p: dict) -> tuple[int, int]:
    """(ブランド×ジャンルの組の数, 仕入れ先1か所あたりの検索語の本数)"""
    pairs = len(p["pairs"])
    q = sum(len(brand_queries(x["brand"])) * len(x["genre"]["queries"]) for x in p["pairs"])
    return pairs, q


def scan(req: dict, settings: dict, sources=None, progress=None, should_stop=None, live=None) -> list[Deal]:
    """フリーリサーチを実行して案件の一覧を返す（Playwright のブラウザを1つ起動して使い回す）。
    live: 途中経過を受け取るオブジェクト（main.scan の説明を参照。省略可。結果には影響しない）"""
    from playwright.sync_api import sync_playwright

    import main as scan_main
    from scrapers import mercari as mercari_scraper

    p = parse_request(req)
    if not p["brands"]:
        raise ValueError("ブランドを入力してください")
    if not p["genres"]:
        raise ValueError("ジャンルを1つ以上選ぶか、自由入力してください")
    started = time.time()
    for k in stats:
        stats[k] = 0
    _memo.clear()
    analyzer.reset_session()
    photo_reader.reset_session(settings)
    ocr_reader.reset_session(settings)
    local_inspector.reset_session(settings)
    analyzer.set_photo_check(photo_reader.condition_enabled() or local_inspector.enabled())
    from scrapers import yahoo_auctions, vector_park, trefac_fashion, rakuma, yahoo_flea_market, yahoo_shopping
    yahoo_auctions.reset_retry_state()
    for mod in (yahoo_auctions, mercari_scraper, vector_park, trefac_fashion, rakuma, yahoo_flea_market, yahoo_shopping):
        mod._image_urls.clear()
    yahoo_shopping._descriptions.clear()
    database.init_db()
    _cache_init()
    for b in p["brands"]:
        ensure_aliases(b)
    # 型番の相場に切り替えてよい (ブランド, ジャンル) の組 = config.json に登録済みのキーワードの組だけ
    # （例: タトラス×ダウン、ルイヴィトン×バッグ。グッチのジャケットにサングラスの型番の相場を使わないため）
    try:
        with open(scan_main.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        p["model_pairs"] = {(kw.get("brand_name") or kw["name"].split()[0], kw.get("category") or "")
                            for kw in cfg.get("keywords", [])}
        # 写真のタグの品番をタイトル・説明文より優先する組（config の prefer_photo_model。カナダグース×ダウン）
        p["photo_pairs"] = {(kw.get("brand_name") or kw["name"].split()[0], kw.get("category") or "")
                            for kw in cfg.get("keywords", []) if kw.get("prefer_photo_model")}
    except Exception:
        p["model_pairs"] = set()
        p["photo_pairs"] = set()

    pairs = p["pairs"]
    print("=" * 50)
    if req.get("list_pairs"):
        print(f"マイブランドでリサーチ開始: ブランド {len(p['brands'])}件（{'・'.join(p['brands'][:8])}"
              f"{' ほか' if len(p['brands']) > 8 else ''}）/ ブランド×ジャンル {len(pairs)}組 / "
              f"仕入れ値 ¥{p['price_min']:,}〜¥{p['price_max']:,}（ブランドごとの指定を優先）/ {p['gender']} / "
              f"仕入れ先 {','.join(sorted(sources or []))}")
    else:
        print(f"フリーリサーチ開始: ブランド {'・'.join(p['brands'])} / ジャンル {'・'.join(g['label'] for g in p['genres'])} / "
              f"仕入れ値 ¥{p['price_min']:,}〜¥{p['price_max']:,} / {p['gender']} / 仕入れ先 {','.join(sorted(sources or []))}")
    print("=" * 50)

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = mercari_scraper.new_page(browser)
            deals: list[Deal] = []
            for i, pair in enumerate(pairs):
                brand, genre = pair["brand"], pair["genre"]
                if should_stop and should_stop():
                    print("\n[中断] ユーザー操作によりリサーチを中断しました")
                    break
                if progress:
                    progress(i, len(pairs), f"{brand} {genre['label']}")
                # この組だけの仕入れ値の範囲・最低利益（仕入れリストのブランドごとの指定）。無ければ画面の条件
                pp, ss = p, settings
                if pair.get("price_min") is not None or pair.get("price_max") is not None:
                    pp = dict(p, price_min=pair["price_min"] if pair.get("price_min") is not None else p["price_min"],
                              price_max=pair["price_max"] if pair.get("price_max") is not None else p["price_max"])
                if pair.get("min_profit") is not None:
                    ss = dict(ss, min_profit_yen=pair["min_profit"])
                if pair.get("per_source"):
                    # マイブランドの「検索数」（このブランドだけの仕入れ先1か所あたりの取得件数）
                    ss = dict(ss, search_items_per_source=pair["per_source"])
                try:
                    deals += _scan_pair(page, brand, genre, pp, ss, sources, i, len(pairs),
                                        progress, should_stop, scan_main, live)
                except Exception as e:
                    if not req.get("list_pairs"):
                        raise
                    # 仕入れリストの一括リサーチでは、1組の失敗で全体を止めない
                    print(f"  [エラー] {brand} × {genre['label']} を飛ばしました: {e}")
            deals = scan_main._check_photo_conditions(deals, settings, page, len(pairs),
                                                      progress=progress, should_stop=should_stop, live=live)
            for d in deals:
                _finish_info(d, settings)
            if req.get("list_pairs"):
                # 型番とモデル名の両方で同じ出品が見つかったときは1件にする
                uniq: dict[str, Deal] = {}
                for d in deals:
                    if d.item.url not in uniq or d.estimated_profit > uniq[d.item.url].estimated_profit:
                        uniq[d.item.url] = d
                deals = list(uniq.values())
            return _merge_cross_listed(deals)
        finally:
            browser.close()
    finally:
        playwright.stop()
        print(f"\n[フリーリサーチ統計] 所要 {(time.time() - started) / 60:.1f}分 / メルカリ売り切れ取得 {stats['sold_fetch']}回"
              f"（キャッシュ {stats['sold_cached']}回）/ 説明文取得 {stats['desc_fetched']}件（キャッシュ {stats['desc_cached']}件）/ "
              f"型番の相場に切り替え {stats['model_upgraded']}件（うち写真のOCR {stats['ocr_upgraded']}件）")
        if ocr_reader.enabled():
            print(f"[フリーリサーチ統計] {ocr_reader.summary()}")
        if local_inspector.stats["inspect_targets"]:
            print(f"[フリーリサーチ統計] {local_inspector.summary()}")


def _scan_pair(page, brand: str, genre: dict, p: dict, settings: dict, sources, pair_index: int, n_pairs: int,
               progress, should_stop, scan_main, live=None) -> list[Deal]:
    label = f"{brand} × {genre['label']}" + (f" × {p['gender']}" if p["gender"] in GENDERS else "")
    print(f"\n■ [{label}] 仕入れ値 ¥{p['price_min']:,}〜¥{p['price_max']:,}")
    like_re = _brand_like_re(brand)
    per_source = settings["search_items_per_source"]
    category = _category_for(genre)

    # 1) 仕入れ先を検索（ブランドのカタカナ名・英字名 × ジャンルの検索語。性別の語は付けない＝タイトルに無い出品も拾う）
    queries = [f"{bq} {w}" for bq in brand_queries(brand) for w in genre["queries"]]
    fetchers = scan_main.source_fetchers(page, settings, p["price_max"], p["price_min"], per_source, [], False)
    fetchers = [(k, f) for k, f in fetchers if sources is None or k in sources]
    n_search = len(fetchers) * len(queries)
    items, seen = [], set()
    no = 0
    for key, fetch in fetchers:
        for q in queries:
            if should_stop and should_stop():
                return []
            no += 1
            if progress:
                progress(pair_index, n_pairs, f"{q}（{scan_main.SOURCE_LABELS[key]}）", no - 1, n_search)
            got = fetch(q)
            new = [it for it in got if it.url and it.url not in seen]
            seen.update(it.url for it in new)
            print(f"  [{scan_main.SOURCE_LABELS[key]}] 「{q}」 {len(got)}件（新規 {len(new)}件）")
            items += new
            time.sleep(1)

    # 2) タイトルで絞る（ブランド・ジャンル・類似品・キッズ等・性別）
    reasons: dict[str, int] = {}
    kept = []
    for it in items:
        r = title_fits(it.title, brand, genre, like_re)
        if r is None:
            g = detect_gender(it.title, brand=brand, genre_key=genre["key"])
            if not gender_ok(g, p["gender"]):
                r = "性別が違う"
        if r:
            reasons[r] = reasons.get(r, 0) + 1
            continue
        kept.append(it)
    if reasons:
        print("  [タイトルで除外] " + " / ".join(f"{k} {v}件" for k, v in reasons.items()))
    print(f"  タイトルの条件に合う出品 {len(kept)}件 / 取得 {len(items)}件")
    if not kept:
        return []

    # 3) Yahoo!ショッピングの送料（送料込みで仕入れ値の範囲を確認し直す）
    kept = scan_main.fill_shopping_postage(kept, settings, should_stop)
    kept = [it for it in kept if p["price_min"] <= it.price <= p["price_max"]]

    # 4) ジャンルの相場（性別を選んだときはその性別の売り切れ。「指定なし」でも出品の性別が分かればその性別の相場）
    markets: dict[str, GenreMarket] = {}

    def market_for(g: str) -> GenreMarket | None:
        want = p["gender"] if p["gender"] in GENDERS else (g if g in GENDERS else "指定なし")
        if want not in markets:
            markets[want] = get_genre_market(page, brand, genre, want, settings)
            s = markets[want].summary
            if s:
                print(f"  [相場] {brand} {genre['label']}（{want}）: 中央値 ¥{s['median']:,} / 中央50% ¥{s['q1']:,}〜¥{s['q3']:,}"
                      f"（売り切れ {s['n']}件・ばらつき {s['spread']:.2f}・信頼度 {s['confidence']}）")
            else:
                print(f"  [相場] {brand} {genre['label']}（{want}）: 売り切れが3件未満のため相場なし")
        m = markets[want]
        if m.summary is None and want != "指定なし":
            # 性別つきで売り切れが少なすぎるときは性別なしの相場で（信頼度は低にする）
            alt = market_for("指定なし") if p["gender"] not in GENDERS else None
            return alt
        return m if m.summary else None

    commission = settings["mercari_commission_rate"]
    ship = settings["assumed_shipping_cost"]
    min_profit = settings["min_profit_yen"]

    # 5) 利益が出うる出品（中央50%の上端 Q3 × 新品の係数で利益が出る）だけ、説明文・写真まで確認する
    potential = []
    for it in kept:
        g = detect_gender(it.title, brand=brand, genre_key=genre["key"])
        m = market_for(g)
        if m is None:
            stats["market_none"] += 1
            continue
        best = int(m.summary["q3"] * 1.30 * (1 - commission)) - ship - it.price
        if best >= min_profit or _model_from(it.title, brand, category, p):
            potential.append(it)
    print(f"  利益が出うる出品 {len(potential)}件 → 説明文を確認します")
    if not potential:
        return []
    descs = _fetch_descriptions(potential, page, should_stop, scan_main)

    deals: list[Deal] = []
    unresolved_for_ocr = []
    info_of: dict[str, dict] = {}
    for it in potential:
        desc = descs.get(it.url) or ""
        g = detect_gender(it.title, desc, brand, genre["key"])
        if not gender_ok(g, p["gender"]):
            continue
        # 説明文で別のアイテム・キッズ等と分かる出品を除く（「キッズ」「まとめ売り」は説明文の定型文にも出るのでタイトルだけで判定済み）
        model = _model_from(it.title, brand, category, p) or (_model_from(desc, brand, category, p) if desc else None)
        info_of[it.url] = {"gender": g, "desc": desc, "model": model}
        if model is None and (brand, category) in p["model_pairs"] and _has_number_format(brand, category):
            unresolved_for_ocr.append(it)

    # 6) 写真のタグ（無料・Mac内OCR）で型番を探す（型番の形式が登録済みのブランドだけ）
    if unresolved_for_ocr and ocr_reader.enabled():
        ocr_models = ocr_reader.read_listings(
            unresolved_for_ocr, brand, category,
            lambda todo: scan_main._get_image_urls(todo, page), should_stop=should_stop)
        for url, model in ocr_models.items():
            if url in info_of:
                info_of[url]["model"] = model
                info_of[url]["model_from"] = "写真のタグ（OCR）"

    # 6b) 写真のタグの品番を優先する組（カナダグース×ダウン）: タイトル・説明文で型番・モデル名が取れた出品も写真を読み、
    #     タグの品番が違えばそれに切り替える（通常のモードの main._scan_keywords と同じ考え方）
    if (brand, category) in p.get("photo_pairs", set()) and ocr_reader.enabled():
        have = [it for it in potential if it.url in info_of and info_of[it.url]["model"]
                and not info_of[it.url].get("model_from")]
        if have:
            tag_numbers = ocr_reader.read_listings(
                have, brand, category, lambda todo: scan_main._get_image_urls(todo, page),
                should_stop=should_stop, numbers_only=True,
                label="写真のタグの品番を優先（タイトル・説明文で型番・モデル名あり）")
            for url, num in tag_numbers.items():
                inf = info_of[url]
                orig = inf["model"]
                if model_extractor.normalize(num) == model_extractor.normalize(orig):
                    continue
                photo_name = model_extractor.number_name(num, brand)
                orig_name = orig if not model_extractor.is_number(orig, brand) else model_extractor.number_name(orig, brand)
                inf["model"] = num
                inf["model_from"] = "写真のタグ（OCR）"
                inf["name_fallback"] = photo_name or orig_name
                if photo_name != orig_name or not photo_name:
                    inf["photo_model"] = {"photo": num, "orig": orig, "photo_name": photo_name or ""}

    for it in potential:
        inf = info_of.get(it.url)
        if inf is None:
            continue
        if should_stop and should_stop():
            break
        gm = market_for(inf["gender"])
        model_market, model_basis = None, None
        if inf["model"]:
            model_market = _model_market(page, brand, inf["model"], settings)
            if model_market is None and inf.get("name_fallback"):
                # 写真の品番の相場が無ければ、品番から引いたモデル名で取り直す（カナダグース。品番が売り切れのタイトルに載ることは少ない）
                inf["model"] = inf["name_fallback"]
                model_market = _model_market(page, brand, inf["model"], settings)
            if model_market is not None:
                stats["model_upgraded"] += 1
                if inf.get("model_from"):
                    stats["ocr_upgraded"] += 1
                model_basis = {"model": inf["model"], "from": inf.get("model_from") or "タイトル・説明文",
                               "n": model_market.sample_count, "median": model_market.median_price,
                               "min": model_market.min_price, "max": model_market.max_price}
        if model_market is None and gm is None:
            continue
        if model_market is not None:
            market = model_market
        else:
            s = gm.summary
            market = MarketPrice(keyword=f"{brand} {genre['label']}", avg_price=s["median"], median_price=s["median"],
                                 min_price=s["min"], max_price=s["max"], sample_count=s["n"])
        deal = analyzer.calculate_deal(it, market.keyword, market, commission_rate=commission,
                                       shipping_cost=ship, min_profit=min_profit)
        if deal is None:
            continue
        deal.brand = brand
        deal.model = inf["model"] if model_market is not None else ""
        deal.category = genre.get("category_label") or genre["label"]
        deal.search_keyword = f"{brand} {genre['label']}"
        feats = detect_item_features(it.title, inf["desc"])
        deal.free_info = _build_info(gm, inf, feats, model_basis, p)
        pm = inf.get("photo_model")
        if pm and model_market is not None and (pm["photo_name"] or inf["model"] == pm["photo"]):
            deal.photo_model = dict(pm, used=inf["model"])   # 結果が変わらない切り替え（品番のモデル名不明・元のモデル名で相場）は表示しない
        deals.append(deal)
        if live:
            live.deal(deal)   # ローカル画面にすぐ出す（結果には影響しない）
    deals.sort(key=lambda d: d.estimated_profit, reverse=True)
    print(f"  --> {len(deals)} 件の案件 [フリーリサーチ]")
    return deals


def _category_for(genre: dict) -> str:
    """model_extractor のジャンル（config の category）。ダウン以外のアパレルは指定なし"""
    return {"down": "ダウン", "bag": "バッグ"}.get(genre["key"], "")


def _has_number_format(brand: str, category: str) -> bool:
    try:
        return model_extractor.has_number_format(brand, category)
    except Exception:
        return False


def _model_from(text: str, brand: str, category: str, p: dict) -> str | None:
    """タイトル・説明文の型番・モデル名（config.json に同じブランド×ジャンルのキーワードがある組だけ）"""
    if not text or not category or (brand, category) not in p.get("model_pairs", set()):
        return None
    try:
        return model_extractor.extract(text, brand, category)
    except Exception:
        return None


def _model_market(page, brand: str, model: str, settings: dict):
    """型番・モデル名の相場（通常のモードと同じ get_market_price。売り切れ3件以上）"""
    name_filter, filter_key, alt = None, "", None
    keyword = f"{brand} {model}"
    if model_extractor.extract_name(model, brand) == model:
        name_filter = (lambda t, _m=model, _b=brand: model_extractor.extract_name(t, _b) == _m)
        filter_key = f"model:{model}"
        names = model_extractor.search_names(model, brand)
        keyword = f"{brand} {names[0]}"
        alt = [f"{brand} {n}" for n in names[1:]]
    return analyzer.get_market_price(
        page, keyword, sample_count=settings["mercari_sold_sample_count"],
        cache_hours=settings["price_cache_hours"], title_filter=name_filter,
        filter_key=filter_key, alt_keywords=alt, min_samples=3)


def _fetch_descriptions(items: list, page, should_stop, scan_main) -> dict[str, str]:
    """説明文（DB の7日キャッシュ → メルカリは Playwright でまとめて → 他は DESCRIPTION_FETCHERS で1件ずつ）"""
    targets = [it for it in items if it.source == "mercari_cheap" or it.source in scan_main.DESCRIPTION_FETCHERS]
    descs = database.get_cached_descriptions([it.url for it in targets], max_age_hours=scan_main.DESCRIPTION_CACHE_HOURS)
    stats["desc_cached"] += len(descs)
    fetched: dict[str, str] = {}
    from scrapers import mercari as mercari_scraper
    murls = [it.url for it in targets if it.source == "mercari_cheap" and it.url not in descs]
    if murls:
        fetched.update(mercari_scraper.get_descriptions(page, murls))
        stats["desc_fetched"] += len(murls)
    last: dict[str, float] = {}
    for it in targets:
        if should_stop and should_stop():
            break
        if it.source not in scan_main.DESCRIPTION_FETCHERS or it.url in descs or it.url in fetched:
            continue
        fn, interval = scan_main.DESCRIPTION_FETCHERS[it.source]
        wait = last.get(it.source, 0) + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        d = fn(it.url)
        last[it.source] = time.time()
        stats["desc_fetched"] += 1
        if d is not None:
            fetched[it.url] = d
    database.cache_descriptions(fetched)
    descs.update(fetched)
    return descs


def _build_info(gm: GenreMarket | None, inf: dict, feats: list[str], model_basis: dict | None, p: dict) -> dict:
    s = gm.summary if gm is not None else None
    if model_basis is not None:
        confidence = "高"
        basis_kind = "model"
    else:
        confidence = s["confidence"] if s else "低"
        basis_kind = "genre"
        # 性別を選ばずに性別が不明な出品は、男女の混ざった相場なので一段下げる
        if p["gender"] not in GENDERS and inf["gender"] not in GENDERS and confidence == "高":
            confidence = "中"
    feat_rows = []
    for f in feats:
        cmp = gm.feature_compare(f["tag"]) if gm is not None else None
        feat_rows.append({**f, **(cmp or {})})
    return {
        "mode": "free",
        "gender": inf["gender"],
        "confidence": confidence,
        "basis": basis_kind,
        "genre_market": ({"n": s["n"], "used": s["used"], "median": s["median"], "q1": s["q1"], "q3": s["q3"],
                          "spread": s["spread"], "queries": gm.queries, "gender": gm.gender,
                          "category_ids": gm.category_ids, "category_label": gm.genre.get("label") if gm.category_ids else "",
                          "confidence": s["confidence"]} if s else None),
        "model_market": model_basis,
        "features": feat_rows,
    }


def _merge_cross_listed(deals: list[Deal], quiet: bool = False) -> list[Deal]:
    """同じ店が複数の仕入れ先に出している同じ商品（ベクトルパークの出品がヤフオク・Yahoo!ショッピングにも出る等。
    タイトルが同じ）は、いちばん安い1件にまとめ、他の仕入れ先を free_info.also_on に書く"""
    def key(d: Deal) -> str:
        t = re.sub(r"【[^】]*】", "", d.item.title)
        t = re.sub(r"[\s/／■◆★☆]+", "", _norm(t))
        return t if len(t) >= 20 else d.item.url
    # Yahoo!ショッピングは同じ出品のタイトルの末尾に「レディース 古着 中古」等を足すことがあるので、
    # 片方のタイトルがもう片方の頭の部分（20文字以上）と同じなら同じ商品とみなす
    groups: dict[str, list[Deal]] = {}
    for d in sorted(deals, key=lambda d: len(key(d))):
        k = key(d)
        hit = next((g for g in groups if len(g) >= 20 and k.startswith(g)), None)
        groups.setdefault(hit or k, []).append(d)
    out = []
    for g in groups.values():
        g.sort(key=lambda d: d.item.price)
        best = g[0]
        if len(g) > 1 and best.free_info is not None:
            best.free_info["also_on"] = [{"source": d.item.source, "url": d.item.url, "price": d.item.price}
                                         for d in g[1:]]
        out.append(best)
    if len(out) < len(deals) and not quiet:
        print(f"[フリーリサーチ] 同じ商品が複数の仕入れ先に出ている {len(deals) - len(out)}件をまとめました（安いほうを表示）")
    return out


def _finish_info(d: Deal, settings: dict) -> None:
    """写真の確認のあとに、下振れ時（中央50%の下端 Q1）の利益を計算して添える"""
    info = d.free_info
    if not info:
        return
    gm = info.get("genre_market")
    if info.get("basis") == "genre" and gm:
        ref = int(gm["q1"] * d.condition_factor)
        net = int(ref * (1 - settings["mercari_commission_rate"])) - settings["assumed_shipping_cost"]
        info["profit_low"] = net - d.item.price
        info["reference_low"] = ref
        ref_hi = int(gm["q3"] * d.condition_factor)
        info["profit_high"] = int(ref_hi * (1 - settings["mercari_commission_rate"])) \
            - settings["assumed_shipping_cost"] - d.item.price
