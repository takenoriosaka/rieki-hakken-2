"""
リサーチ結果カードの「メルカリ相場を見る」「📋 コピー」で使う検索語（2026-11-22 追加）

案件1件 → {"keyword": 検索語, "category_ids": [メルカリのカテゴリID]}。
画面（local_ui/research.js）はこれを売り切れ・新しい順の検索URLにする。フィールドが無い昔の結果は従来の方式。

検索語は最大4語で、0件になりにくい順に組み立てる:
  1. ブランド名（必ず）
  2. モデル名（例: スピーディ30）。無ければ型番（例: RB2140）。どちらも無ければ省略
     - ルイヴィトンのバッグの型番（M41526）とダウンの品番（カナダグース・モンクレール・タトラス）は、
       ツールが知っているモデル名に置き換える（M41526 → スピーディ30。ダミエ等の柄は4語目に付ける）
  3. 種類（ハンドバッグ・ショルダーバッグ・トートバッグ・リュック・テーラードジャケット・長財布 等。タイトルから判定し
     メルカリでよく使われる表記にそろえる）。型番だけのとき（モデル名に置き換えられないとき）は付けない
     （型番で十分に絞れていて、種類の語を足すと0件になりやすいため）。モデル名に種類の語が入っていれば付けない
  4. 高値要素・特徴を1つ（free_research.detect_features をタイトルに使い、メルカリの検索で意味のあるものだけ）。
     モデル名・型番があるときは付けない（絞りすぎて0件になりやすいため）
category_id はフリーリサーチの相場と同じものを、種類の語と同じジャンルのとき（または種類が分からないとき）だけ残す。
"""

from __future__ import annotations

import re
import unicodedata

import free_research
import model_extractor


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").upper()


# ── 種類（上から順に見て最初に当たったもの）: (正規表現, 検索語, free_research のジャンル key or None) ──
# 具体的な語を先に（ダウンジャケット → ジャケット、マウンテンパーカー → パーカー の順）
_K = r"(?<![ァ-ヶー])"     # 直前がカタカナでない
_TYPE_RULES_SRC: list[tuple[str, str, str | None]] = [
    # セットアップ・スーツ
    (r"セットアップ|SET\s?UP", "セットアップ", "setup"),
    (r"タキシード|TUXEDO", "タキシード", "setup"),
    (r"スーツ(?!ケース|カバー)|(?<![A-Z])SUIT(?![A-Z])", "スーツ", "setup"),
    # ダウン・中綿
    (r"ダウン\s*ベスト|DOWN\s*VEST", "ダウンベスト", "down"),
    (r"ダウン\s*コート|DOWN\s*COAT", "ダウンコート", "down"),
    (r"ダウン|(?<![A-Z])DOWN(?![A-Z])", "ダウンジャケット", "down"),
    (r"中綿", "中綿ジャケット", "down"),
    # コート
    (r"トレンチ|TRENCH", "トレンチコート", "coat"),
    (r"チェスター", "チェスターコート", "coat"),
    (r"ステンカラ", "ステンカラーコート", "coat"),
    (r"ピーコート|(?<![A-Z])P\s?コート|PEA\s?COAT", "ピーコート", "coat"),
    (r"ダッフル|DUFFLE", "ダッフルコート", "coat"),
    (r"モッズコート", "モッズコート", "coat"),
    (r"ムートンコート", "ムートンコート", "coat"),
    (r"コート|(?<![A-Z])COAT(?![A-Z])", "コート", "coat"),
    # ジャケット
    (r"テーラード|TAILORED|ブレザー|BLAZER", "テーラードジャケット", "jacket"),
    (r"ノーカラージャケット", "ノーカラージャケット", "jacket"),
    (r"ライダース|RIDERS", "ライダースジャケット", "jacket"),
    (r"レザージャケット|LEATHER\s*JACKET", "レザージャケット", "jacket"),
    (r"Gジャン|ジージャン|デニムジャケット", "デニムジャケット", "jacket"),
    (r"(?<![A-Z0-9])MA-?1(?![0-9])", "MA-1", "jacket"),
    # その他アウター
    (r"マウンテンパーカー|MOUNTAIN\s?PARKA", "マウンテンパーカー", "outer"),
    (r"スカジャン", "スカジャン", "outer"),
    (r"スタジャン|スタジアムジャンパー", "スタジャン", "outer"),
    (r"ブルゾン|BLOUSON|ジャンパー(?!スカート)", "ブルゾン", "outer"),
    (r"ジャケット|JACKET", "ジャケット", "jacket"),
    # バッグ（2WAY でハンド・ショルダーの両方があればハンドバッグ）
    (r"クラッチ|CLUTCH|セカンドバッグ|セカンドバック", "クラッチバッグ", "bag"),
    (r"ボストン|BOSTON", "ボストンバッグ", "bag"),
    (r"リュック|バックパック|BACKPACK|デイパック|DAYPACK", "リュック", "bag"),
    (r"ボディ\s*バッ[グク]|ウエスト\s*(?:バッ[グク]|ポーチ)|BODY\s*BAG|WAIST\s*BAG|スリングバッ[グク]", "ボディバッグ", "bag"),
    (r"ブリーフケース|ビジネスバッ[グク]|BRIEFCASE", "ビジネスバッグ", "bag"),
    (r"トート|TOTE", "トートバッグ", "bag"),
    (r"ハンドバッ[グク]|HANDBAG|手提げ|" + _K + r"ハンド(?!ル|メイド|メード|ウォーマー|クリーム|タオル|ミラー)",
     "ハンドバッグ", "bag"),
    (r"ショルダー(?!ストラップ|パッド|ベルト|紐)|SHOULDER\s*BAG|斜め?[掛が]け|肩掛け|ポシェット|POCHETTE|"
     r"クロスボディ|メッセンジャー|MESSENGER", "ショルダーバッグ", "bag"),
    (r"ポーチ|POUCH", "ポーチ", "bag"),
    # 財布・小物
    (r"長財布|ロングウォレット|LONG\s*WALLET", "長財布", "komono"),
    (r"二つ折り?財布|2つ折り?財布|二つ折り|2つ折り", "二つ折り財布", "komono"),
    (r"三つ折り?財布|3つ折り?財布|三つ折り|3つ折り", "三つ折り財布", "komono"),
    (r"コインケース|小銭入れ|COIN\s*CASE", "コインケース", "komono"),
    (r"キーケース|KEY\s*CASE", "キーケース", "komono"),
    (r"名刺入れ|名刺ケース", "名刺入れ", "komono"),
    (r"カードケース|パスケース|定期入れ|CARD\s*CASE", "カードケース", "komono"),
    (r"財布|ウォレット|WALLET", "財布", "komono"),
    (r"ネクタイ|NECKTIE", "ネクタイ", "tie"),
    (r"マフラー|MUFFLER", "マフラー", "komono"),
    (r"ストール|STOLE", "ストール", "komono"),
    # トップス
    (r"マウンテン", "マウンテンパーカー", "outer"),
    (r"パーカー|フーディ|HOODIE", "パーカー", "tops"),
    (r"スウェット|トレーナー|SWEAT", "スウェット", "tops"),
    (r"カーディガン|CARDIGAN", "カーディガン", "tops"),
    (r"ニット|セーター|KNIT|SWEATER", "ニット", "tops"),
    (r"ポロシャツ|POLO\s*SHIRT", "ポロシャツ", "tops"),
    (r"Tシャツ|TEE(?![A-Z])|カットソー", "Tシャツ", "tops"),
    (r"ブラウス|BLOUSE", "ブラウス", "tops"),
    (r"シャツ|SHIRT", "シャツ", "tops"),
    (r"ジレ|GILET|" + _K + r"ベスト(?!セラー|プライス)", "ベスト", "tops"),
    # ボトムス・ワンピース
    (r"スラックス|SLACKS|トラウザー", "スラックス", "bottoms"),
    (r"ジーンズ|ジーパン|デニムパンツ|JEANS", "デニムパンツ", "bottoms"),
    (r"スカート|SKIRT", "スカート", "bottoms"),
    (r"パンツ|PANTS", "パンツ", "bottoms"),
    (r"ワンピース|ドレス(?!シャツ|シューズ)|DRESS(?!\s*SHIRT|\s*SHOES)", "ワンピース", "onepiece"),
    # 靴
    (r"スニーカー|SNEAKER", "スニーカー", "shoes"),
    (r"ローファー|LOAFER", "ローファー", "shoes"),
    (r"ブーツ|BOOTS", "ブーツ", "shoes"),
    (r"パンプス|PUMPS", "パンプス", "shoes"),
    (r"サンダル|SANDAL", "サンダル", "shoes"),
    (r"ドレスシューズ|ビジネスシューズ|革靴|DRESS\s*SHOES", "ドレスシューズ", "shoes"),
    # ジャンルの無いもの（サングラス・時計・アクセサリー）
    (r"サングラス|SUNGLASS", "サングラス", None),
    (r"腕時計|時計|ウォッチ|WATCH", "腕時計", None),
    (r"ネックレス|NECKLACE|ペンダント", "ネックレス", None),
    (r"ブレスレット|BRACELET|バングル|BANGLE", "ブレスレット", None),
    (r"ピアス|イヤリング|EARRING", "ピアス", None),
    (r"指輪", "指輪", None),
]
_TYPE_RULES = [(re.compile(rx), word, key) for rx, word, key in _TYPE_RULES_SRC]
# 「ボタンダウン」「ダウンロード」等はダウンとみなさない
_NOT_TYPE_RE = free_research._NOT_OUTER_RE


def item_type(title: str) -> tuple[str, str | None] | None:
    """タイトル → (種類の検索語, ジャンル key)。判定できなければ None"""
    t = _NOT_TYPE_RE.sub(" ", _norm(title))
    for rx, word, key in _TYPE_RULES:
        if rx.search(t):
            return word, key
    return None


# ── 特徴（free_research.FEATURES のタグ → 検索語）。上ほど優先。ここに無いタグは検索語に使わない ──
# 使わないもの: サイズ（XL）・ボタン数・裏地・柄（言い方がばらばら）・丈・国名・ライン名の英字（LONDON 等。カタカナ/英字で
# 書き方が分かれて0件になりやすい）・付属品・ヴィンテージ（ビンテージと書き分けられる）
_FEATURE_WORDS: list[tuple[str, str]] = [
    ("ノバチェック", "ノバチェック"),
    ("メガチェック", "メガチェック"),
    ("ブラックレーベル", "ブラックレーベル"),
    ("ブルーレーベル", "ブルーレーベル"),
    ("スリーピース", "スリーピース"),
    ("カシミヤ", "カシミヤ"),
    ("ムートン", "ムートン"),
    ("ラムレザー", "ラムレザー"),
    ("ツイード", "ツイード"),
    ("ヘリンボーン", "ヘリンボーン"),
    ("モヘヤ", "モヘア"),
    ("ベロア", "ベロア"),
    ("シルク", "シルク"),
    ("ダブルボタン", "ダブル"),
    ("リバーシブル", "リバーシブル"),
    ("金ボタン", "金ボタン"),
    ("ファー付き", "ファー"),
    ("ライナー付き", "ライナー"),
    ("未使用・タグ付き", "未使用"),
]


def feature_word(title: str, type_word: str = "") -> str | None:
    tags = set(free_research.detect_features(title))
    for tag, word in _FEATURE_WORDS:
        if tag not in tags or word in type_word:          # ムートンコート + ムートン 等は重ねない
            continue
        if word == "シルク" and type_word == "ネクタイ":   # ネクタイはほぼシルクなので絞り込みにならない
            continue
        return word
    return None


# ── モデル名・型番 ──
_LV_VARIANT_RE = re.compile(r"^(.+?)（(ダミエ[^）]*)）$")


def _model_parts(brand: str, model: str, title: str, category: str) -> tuple[str, str, bool]:
    """(モデル名 or 型番, 柄などの4語目, モデル名か)。どちらも無ければ ("", "", False)"""
    model = (model or "").strip()
    if not model and brand:
        # フリーリサーチ等で型番・モデル名が付いていない案件は、登録済みのモデル名だけタイトルから引く（型番は誤認を避けて見ない）
        try:
            if brand == "ルイヴィトン":
                if category == "バッグ" or (item_type(title) or ("", ""))[1] == "bag":
                    model = model_extractor.extract(title, brand, "バッグ") or ""
            else:
                model = model_extractor.extract_name(title, brand) or ""
        except Exception:
            model = ""
    if not model:
        return "", "", False
    # 型番 → モデル名（ルイヴィトンのバッグ・ダウンの品番）
    if brand == "ルイヴィトン":
        label = model_extractor._LV_BAG_NUMBERS.get(model.upper())
        if label:
            m = _LV_VARIANT_RE.match(label)
            if m:
                return m.group(1), m.group(2), True
            if "（" not in label:
                return label, "", True
            return label.split("（")[0], "", True
    try:
        name = model_extractor.number_name(model, brand)
    except Exception:
        name = None
    if name:
        return name, "", True
    try:
        is_name = model_extractor.extract_name(model, brand) == model
    except Exception:
        is_name = False
    if not is_name and re.search(r"\d", model) and re.fullmatch(r"[A-Z0-9][A-Z0-9.\-/ ]*", _norm(model)):
        return model, "", False                     # 型番
    return model, "", True                          # モデル名（登録済み・マイブランドの自由入力）


_BAGLIKE_IN_NAME = re.compile(r"ポシェット|バッ[グク]|トート|ボストン|リュック|ポーチ|クラッチ|ショルダー|メッセンジャー|"
                              r"サック|キーポル|ダウン|コート|ジャケット|ベスト|ジレ|パーカ")


def _genre_key_of(free_info: dict | None) -> str | None:
    gm = (free_info or {}).get("genre_market") or {}
    label = gm.get("category_label") or ""
    for g in free_research.GENRES:
        if g["label"] == label:
            return g["key"]
    return None


def build(brand: str, model: str, title: str, category: str = "", free_info: dict | None = None,
          keyword: str = "") -> dict:
    """検索語と category_id。brand が空なら案件のキーワードをそのまま使う"""
    brand = (brand or "").strip()
    gm = (free_info or {}).get("genre_market") or {}
    cats = list(gm.get("category_ids") or []) if gm else []
    if not brand:
        return {"keyword": (keyword or "").strip(), "category_ids": cats}

    name, variant, is_name = _model_parts(brand, model, title, category)
    typ = item_type(title)
    type_word, type_key = typ if typ else ("", None)

    words = [brand]
    if name:
        words.append(name)
        if is_name:
            if type_word and not _BAGLIKE_IN_NAME.search(name):
                words.append(type_word)
            else:
                type_word, type_key = "", None
            if variant:
                words.append(variant)
        else:
            type_word, type_key = "", None          # 型番だけ: 種類・特徴は足さない
    else:
        if type_word:
            words.append(type_word)
        feat = feature_word(title, type_word)
        if feat:
            words.append(feat)

    # 同じ語の重複（ブランド名にモデル名が含まれる等）を除き、最大4語
    out: list[str] = []
    for w in words:
        if w and all(_norm(w) != _norm(x) for x in out):
            out.append(w)
    out = out[:4]

    # category_id: 種類の語とジャンルが合うとき（種類が分からないとき）だけ残す
    if cats and type_word:
        gkey = _genre_key_of(free_info)
        if gkey and type_key != gkey:
            cats = []
    return {"keyword": " ".join(out), "category_ids": cats}


def for_deal(d) -> dict | None:
    """models.Deal → build の結果（失敗したら None。画面は従来の方式に戻る）"""
    try:
        return build(d.brand, d.model, d.item.title, d.category or "", d.free_info, d.keyword)
    except Exception:
        return None
