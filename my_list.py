"""
マイブランド登録（旧名: 仕入れリスト。自分で育てるブランド・ジャンルのリスト）

・保存先: このフォルダの my_list.json（.gitignore 済み。共有・配布しない）
・ローカル画面のヘッダー「マイブランド登録」から追加・編集・削除・オン/オフする（app.py の /api/mylist*）
・「マイブランドでリサーチ」: オンのブランド（または1グループ／1ブランド）を、フリーリサーチの仕組み
  （free_research.scan）で ブランド × そのブランドのジャンル ごとに順番に調べる
・学校のブランド一覧（course_brands.json）の取り込み: import_course()（コマンドは import_brands.py）

my_list.json の形:
{
  "version": 1,
  "entries": [{"id", "name", "name_en", "aliases": [], "group", "genres": [], "enabled", "memo",
               "model_numbers": [], "models": [], "price_min", "price_max", "per_source",
               "min_profit", "origin", "created_at", "updated_at"}],
  "custom_genres": ["時計", ...],                 # 自分で追加したジャンル（何度でも使える）
  "group_defaults": {"ハイブランド": ["bag", "komono"], ...},  # ジャンル未設定のブランドに使うジャンル
  "seeded": true                                  # 見本（SAMPLE_ENTRIES）を入れたか（1回だけ。消しても入れ直さない）
}
見本の entries は "sample": true（画面に「見本」バッジ）。編集すると sample は外れる。
画面（2026-11-16 ユーザー要望）で入力するのは7つ: name（ブランド）・genres（ジャンル）・model_numbers（型番）・
models（モデル名）・price_min（下限金額）・price_max（上限金額）・per_source（検索数＝仕入れ先1か所あたりの取得件数。
null＝リサーチ画面の取得件数）。新しいキーが無い昔の分もそのまま読める（無ければ空として扱う）。
name_en は追加・改名のときに auto_name_en（ツールの表 EN_QUERY / BRAND_ALIASES / EXTRA_ALIASES）で自動で入れる。
group・aliases・memo・min_profit は JSON に残る（取り込み import_course 用・昔の分）が画面には出さず、min_profit はリサーチにも使わない。
型番・モデル名があるブランドは「ブランド + 型番/モデル名」で1つずつ調べ、タイトルにそれがある出品だけを残す（free_research.model_genre）。
genres の要素は free_research.GENRES の key（"bag" 等）か、自分で追加したジャンルの名前（"時計" 等）。
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime

import free_research
import model_extractor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIST_PATH = os.path.join(BASE_DIR, "my_list.json")

GROUPS = ["ハイブランド", "ミドルハイ・デザイナーズ", "ミドル", "カジュアル", "紳士", "レディース",
          "その他小物", "ドメスティック", "セレクトショップ", "自分で追加"]
OWN_GROUP = "自分で追加"

_APPAREL = ["jacket", "coat", "tops", "bottoms"]
# ジャンル未設定のブランドをリサーチするときに使うジャンル（画面で変えられる。保存は my_list.json の group_defaults）
DEFAULT_GROUP_GENRES: dict[str, list[str]] = {
    "ハイブランド": ["bag", "komono"],
    "ミドルハイ・デザイナーズ": list(_APPAREL),
    "ミドル": list(_APPAREL),
    "カジュアル": ["jacket", "down", "tops", "bottoms"],
    "紳士": ["setup", "jacket", "coat", "tie"],
    "レディース": ["onepiece", "tops", "bottoms", "coat"],
    "その他小物": ["komono", "bag"],
    "ドメスティック": list(_APPAREL),
    "セレクトショップ": list(_APPAREL),
    "自分で追加": list(_APPAREL),
}

# 取り込み時のジャンル名の読み替え（学校の一覧の書き方 → GENRES の key。1語で複数のこともある）
_GENRE_SYNONYMS: dict[str, list[str]] = {
    "セットアップ": ["setup"], "スーツ": ["setup"], "セットアップスーツ": ["setup"],
    # アウターはメルカリの「ジャケット・アウター」の小分類の名前で振り分け（free_research.outer_class と同じ）
    "ジャケット": ["jacket"], "ライダース": ["jacket"], "テーラード": ["jacket"], "テーラードジャケット": ["jacket"],
    "ノーカラージャケット": ["jacket"], "レザージャケット": ["jacket"], "レザージャケットライダース": ["jacket"],
    "Gジャン": ["jacket"], "デニムジャケット": ["jacket"], "Gジャンデニムジャケット": ["jacket"],
    "ミリタリージャケット": ["jacket"], "ミリタリー": ["jacket"], "MA-1": ["jacket"], "MA1": ["jacket"],
    "フライトジャケット": ["jacket"], "MA-1フライトジャケット": ["jacket"], "ナイロンジャケット": ["jacket"],
    "フリースジャケット": ["jacket"], "ボアジャケット": ["jacket"], "ビジネスジャケット": ["jacket"],
    "その他アウター": ["outer"], "ブルゾン": ["outer"], "ジャンパー": ["outer"], "ジャンパーブルゾン": ["outer"],
    "マウンテンパーカー": ["outer"], "スタジャン": ["outer"], "スカジャン": ["outer"], "カバーオール": ["outer"],
    "コート": ["coat"], "トレンチ": ["coat"], "トレンチコート": ["coat"], "ポンチョ": ["coat"], "ケープ": ["coat"],
    "チェスターコート": ["coat"], "ステンカラーコート": ["coat"], "ピーコート": ["coat"], "モッズコート": ["coat"],
    "ダッフルコート": ["coat"],
    "ダウン": ["down"], "中綿": ["down"], "ダウン中綿": ["down"], "ダウンジャケット": ["down"], "ダウンコート": ["down"],
    "ダウンベスト": ["down"], "キルティング": ["down"], "キルティングジャケット": ["down"],
    "アウター": ["jacket", "coat", "down", "outer"],
    "トップス": ["tops"], "シャツ": ["tops"], "ニット": ["tops"], "セーター": ["tops"], "カーディガン": ["tops"],
    "カットソー": ["tops"], "Tシャツ": ["tops"], "パーカー": ["tops"], "スウェット": ["tops"], "ブラウス": ["tops"],
    "ボトムス": ["bottoms"], "パンツ": ["bottoms"], "スカート": ["bottoms"], "デニム": ["bottoms"],
    "ジーンズ": ["bottoms"], "スラックス": ["bottoms"],
    "ワンピース": ["onepiece"], "ドレス": ["onepiece"],
    "バッグ": ["bag"], "鞄": ["bag"], "かばん": ["bag"], "カバン": ["bag"], "バック": ["bag"],
    "靴": ["shoes"], "シューズ": ["shoes"], "スニーカー": ["shoes"], "ブーツ": ["shoes"], "革靴": ["shoes"],
    "小物": ["komono"], "財布": ["komono"], "ウォレット": ["komono"], "アクセサリー": ["komono"], "ベルト": ["komono"],
    "ネクタイ": ["tie"], "タイ": ["tie"], "ボウタイ": ["tie"], "蝶ネクタイ": ["tie"], "マフラー": ["komono"], "ストール": ["komono"], "スカーフ": ["komono"], "帽子": ["komono"],
    "小物アクセサリー": ["komono"], "服飾小物": ["komono"], "革小物": ["komono"],
    "アパレル": list(_APPAREL), "洋服": list(_APPAREL), "服": list(_APPAREL), "衣類": list(_APPAREL),
    "衣料": list(_APPAREL), "ウェア": list(_APPAREL),
}

# リストが空のとき最初に1回だけ入れる見本（画面が空にならないように。消しても入れ直さない）
SAMPLE_ENTRIES = [
    {"name": "ルイヴィトン", "name_en": "LOUIS VUITTON", "group": "ハイブランド", "genres": ["bag"]},
    {"name": "ポールスミス", "name_en": "PAUL SMITH", "group": "紳士", "genres": ["jacket"]},
]

_lock = threading.RLock()


# ──────────────────────────────────────────────────────────────────────────────
# 読み書き
# ──────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _empty() -> dict:
    return {"version": 1, "entries": [], "custom_genres": [],
            "group_defaults": {g: list(v) for g, v in DEFAULT_GROUP_GENRES.items()}}


def load() -> dict:
    with _lock:
        try:
            with open(LIST_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return _empty()
        if not isinstance(data, dict):
            return _empty()
        data.setdefault("version", 1)
        data.setdefault("entries", [])
        data.setdefault("custom_genres", [])
        gd = data.setdefault("group_defaults", {})
        for g, v in DEFAULT_GROUP_GENRES.items():
            gd.setdefault(g, list(v))
        if not data.get("seeded"):
            _seed(data)
        return data


def _seed(data: dict) -> None:
    """見本を1回だけ入れる（すでにブランドがあれば入れずに印だけ付ける）"""
    if not data["entries"]:
        for s in SAMPLE_ENTRIES:
            data["entries"].append({
                "id": uuid.uuid4().hex[:10], "name": s["name"], "name_en": s["name_en"], "aliases": [],
                "group": s["group"], "genres": list(s["genres"]), "enabled": True, "memo": "",
                "price_min": None, "price_max": None, "min_profit": None, "origin": "sample", "sample": True,
                "created_at": _now(), "updated_at": _now()})
    data["seeded"] = True
    save(data)


def save(data: dict) -> None:
    with _lock:
        tmp = LIST_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LIST_PATH)


# ──────────────────────────────────────────────────────────────────────────────
# 名前・ジャンルの整え方
# ──────────────────────────────────────────────────────────────────────────────

def normalize_name(s: str) -> str:
    """重複チェック用（全角半角・大文字小文字・空白・中黒・記号・ヴ/ビ の違いを無視）"""
    t = unicodedata.normalize("NFKC", str(s or "")).upper()
    t = re.sub(r"[\s・･.\-_'’`/／&＆+,，、()（）]", "", t)
    for a, b in (("ヴァ", "バ"), ("ヴィ", "ビ"), ("ヴェ", "ベ"), ("ヴォ", "ボ"), ("ヴ", "ブ"), ("ー", "")):
        t = t.replace(a, b)
    return t


def _split_words(v) -> list[str]:
    if isinstance(v, list):
        out = []
        for x in v:
            out += _split_words(x)
        return out
    return [w.strip() for w in re.split(r"[,、，\n]+", str(v or "")) if w.strip()]


def genre_tokens(word: str) -> list[str]:
    """ジャンルの書き方 → 保存する形（GENRES の key か、自分のジャンル名）。「バッグ・財布」のような複合も分ける"""
    w = unicodedata.normalize("NFKC", str(word or "")).strip()
    if not w:
        return []
    if w in free_research.GENRE_BY_KEY:
        return [w]
    for g in free_research.GENRES:
        if g["label"] == w:
            return [g["key"]]
    key = re.sub(r"[\s・･/／]", "", w)
    if key in _GENRE_SYNONYMS:
        return list(_GENRE_SYNONYMS[key])
    parts = [x for x in re.split(r"[・･/／&＆]+", w) if x.strip()]
    if len(parts) > 1:
        out = []
        for x in parts:
            for t in genre_tokens(x):
                if t not in out:
                    out.append(t)
        return out
    return [w[len("custom:"):] if w.startswith("custom:") else w]


def _clean_genres(v) -> list[str]:
    out: list[str] = []
    for w in _split_words(v):
        for t in genre_tokens(w):
            if t and t not in out:
                out.append(t)
    return out


def genre_label(tok: str) -> str:
    g = free_research.GENRE_BY_KEY.get(tok)
    return g["label"] if g else tok


PER_SOURCE_CHOICES = (20, 50, 80)   # 画面の「検索数」（research.js の PER_SOURCE_PRESETS と同じ）


def _split_terms(v, spaces: bool) -> list[str]:
    """型番・モデル名の入力 → 一覧（「、」「,」改行で区切る。型番は空白でも区切る）。全角は半角に"""
    if isinstance(v, list):
        v = "\n".join(str(x) for x in v)
    t = unicodedata.normalize("NFKC", str(v or ""))
    parts = re.split(r"[,、，\n]+" if not spaces else r"[,、，\s]+", t)
    out = []
    for w in parts:
        w = re.sub(r"\s+", " ", w).strip()
        if spaces:
            w = w.upper()
        if w and w not in out:
            out.append(w)
    return out


def entry_terms(e: dict) -> list[str]:
    """リサーチで使う型番・モデル名（モデル名 → 型番の順）"""
    out = []
    for w in list(e.get("models") or []) + list(e.get("model_numbers") or []):
        if w and w not in out:
            out.append(w)
    return out


def _int_or_none(v):
    try:
        if v in (None, ""):
            return None
        n = int(float(v))
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def effective_genres(entry: dict, data: dict) -> tuple[list[str], bool]:
    """リサーチに使うジャンル。(ジャンル, グループの既定を使ったか)"""
    if entry.get("genres"):
        return list(entry["genres"]), False
    gd = data.get("group_defaults") or {}
    g = gd.get(entry.get("group")) or DEFAULT_GROUP_GENRES.get(entry.get("group")) or list(_APPAREL)
    return list(g), True


# ──────────────────────────────────────────────────────────────────────────────
# 追加・編集・削除・オン/オフ
# ──────────────────────────────────────────────────────────────────────────────

def _find(data: dict, entry_id: str) -> dict | None:
    return next((e for e in data["entries"] if e.get("id") == entry_id), None)


def _dupe(data: dict, name: str, except_id: str | None = None) -> dict | None:
    n = normalize_name(name)
    for e in data["entries"]:
        if e.get("id") == except_id:
            continue
        if n and (normalize_name(e.get("name")) == n or normalize_name(e.get("name_en")) == n):
            return e
    return None


def _apply_fields(e: dict, f: dict, data: dict) -> None:
    if "name" in f:
        e["name"] = str(f.get("name") or "").strip()
    if "name_en" in f:
        e["name_en"] = str(f.get("name_en") or "").strip()
    if "aliases" in f:
        e["aliases"] = [a for a in dict.fromkeys(_split_words(f.get("aliases"))) if a]
    if "group" in f:
        g = str(f.get("group") or "").strip()
        e["group"] = g if g in GROUPS else OWN_GROUP
    if "genres" in f:
        e["genres"] = _clean_genres(f.get("genres"))
        for t in e["genres"]:
            if t not in free_research.GENRE_BY_KEY and t not in data["custom_genres"]:
                data["custom_genres"].append(t)
    if "enabled" in f:
        e["enabled"] = bool(f.get("enabled"))
    if "memo" in f:
        e["memo"] = str(f.get("memo") or "").strip()
    for k in ("price_min", "price_max", "min_profit"):
        if k in f:
            e[k] = _int_or_none(f.get(k))
    if "model_numbers" in f:
        e["model_numbers"] = _split_terms(f.get("model_numbers"), spaces=True)
    if "models" in f:
        e["models"] = _split_terms(f.get("models"), spaces=False)
    if "per_source" in f:
        n = _int_or_none(f.get("per_source"))
        e["per_source"] = max(1, min(n, 100)) if n else None


def add_entry(fields: dict) -> dict:
    with _lock:
        data = load()
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("ブランド名を入力してください")
        d = _dupe(data, name)
        if d:
            raise ValueError(f"「{d['name']}」はもう登録されています")
        e = {"id": uuid.uuid4().hex[:10], "name": name, "name_en": "", "aliases": [], "group": OWN_GROUP,
             "genres": [], "enabled": True, "memo": "", "model_numbers": [], "models": [],
             "price_min": None, "price_max": None, "per_source": None, "min_profit": None,
             "origin": "user", "created_at": _now(), "updated_at": _now()}
        _apply_fields(e, fields, data)
        if e["price_min"] and e["price_max"] and e["price_min"] > e["price_max"]:
            raise ValueError("下限金額が上限金額より大きくなっています")
        if not e.get("name_en"):
            e["name_en"] = auto_name_en(e)
        data["entries"].append(e)
        save(data)
        return e


def update_entry(entry_id: str, fields: dict) -> dict:
    with _lock:
        data = load()
        e = _find(data, entry_id)
        if e is None:
            raise KeyError("そのブランドは見つかりません（削除済みかもしれません）")
        if "name" in fields:
            name = str(fields.get("name") or "").strip()
            if not name:
                raise ValueError("ブランド名を入力してください")
            d = _dupe(data, name, except_id=entry_id)
            if d:
                raise ValueError(f"「{d['name']}」はもう登録されています")
        renamed = "name" in fields and normalize_name(fields.get("name")) != normalize_name(e.get("name"))
        before = dict(e)
        _apply_fields(e, fields, data)
        if e.get("price_min") and e.get("price_max") and e["price_min"] > e["price_max"]:
            e.clear()
            e.update(before)
            raise ValueError("下限金額が上限金額より大きくなっています")
        if renamed:
            # 画面では英語のつづり・別名は入力しない。名前を変えたら、ツールが知っている表記から付け直す
            if "name_en" not in fields:
                e["name_en"] = auto_name_en(e, ignore_en=True)
            if "aliases" not in fields:
                e["aliases"] = []
        e.pop("sample", None)   # 編集したら見本ではなく自分のブランド
        e["updated_at"] = _now()
        save(data)
        return e


def delete_entry(entry_id: str) -> None:
    with _lock:
        data = load()
        n = len(data["entries"])
        data["entries"] = [e for e in data["entries"] if e.get("id") != entry_id]
        if len(data["entries"]) == n:
            raise KeyError("そのブランドは見つかりません（削除済みかもしれません）")
        save(data)


def set_enabled(enabled: bool, ids: list[str] | None = None, group: str | None = None) -> int:
    """ids のブランド、または group のブランド全部（group="" なら全ブランド）をオン/オフ。変えた件数を返す"""
    with _lock:
        data = load()
        n = 0
        idset = set(ids or [])
        for e in data["entries"]:
            hit = (e.get("id") in idset) if ids is not None else (group in (None, "") or e.get("group") == group)
            if hit and bool(e.get("enabled")) != bool(enabled):
                e["enabled"] = bool(enabled)
                e["updated_at"] = _now()
                n += 1
        save(data)
        return n


def add_custom_genre(label: str) -> str:
    with _lock:
        data = load()
        toks = genre_tokens(label)
        if not toks:
            raise ValueError("ジャンル名を入力してください")
        if len(toks) == 1 and toks[0] in free_research.GENRE_BY_KEY:
            return toks[0]   # すでにある基本のジャンル（「バッグ」等）
        t = toks[0] if len(toks) == 1 else str(label).strip()
        if t not in data["custom_genres"]:
            data["custom_genres"].append(t)
            save(data)
        return t


def remove_custom_genre(label: str) -> None:
    """自分のジャンルを候補から外す（すでにブランドに付いている分はそのまま残す）"""
    with _lock:
        data = load()
        data["custom_genres"] = [g for g in data["custom_genres"] if g != label]
        save(data)


def set_group_defaults(group: str, genres) -> list[str]:
    with _lock:
        data = load()
        if group not in GROUPS:
            raise ValueError("グループが正しくありません")
        toks = _clean_genres(genres)
        data["group_defaults"][group] = toks
        for t in toks:
            if t not in free_research.GENRE_BY_KEY and t not in data["custom_genres"]:
                data["custom_genres"].append(t)
        save(data)
        return toks


def view() -> dict:
    """画面用（リスト全体＋ジャンルの候補＋グループの既定）"""
    data = load()
    genres = [{"key": g["key"], "label": g["label"], "icon": g["icon"], "queries": len(g["queries"]),
               "hint": g.get("hint", "")} for g in free_research.GENRES]
    entries = []
    table = _all_alias_table()
    for e in data["entries"]:
        eff, used_default = effective_genres(e, data)
        key = canonical_brand(e, table)
        entries.append({"model_numbers": [], "models": [], "per_source": None, **e,
                        "effective_genres": eff, "uses_default": used_default,
                        "brand_key": key,
                        "en_query": bool(_en_query_for(e) or free_research.EN_QUERY.get(key))})
    return {"groups": GROUPS, "entries": entries, "custom_genres": data["custom_genres"],
            "per_source_choices": list(PER_SOURCE_CHOICES),
            "group_defaults": data["group_defaults"], "builtin_genres": genres,
            "en_brands": sorted(free_research.EN_QUERY)}


# ──────────────────────────────────────────────────────────────────────────────
# 学校のブランド一覧の取り込み
# ──────────────────────────────────────────────────────────────────────────────

def _map_group(g: str) -> str:
    g = str(g or "").strip()
    if g in GROUPS:
        return g
    n = normalize_name(g)
    for x in GROUPS:
        if normalize_name(x) == n:
            return x
    for x in GROUPS:   # 「ミドルハイ」「デザイナーズ」など一部だけの書き方
        if n and (n in normalize_name(x) or normalize_name(x) in n):
            return x
    return OWN_GROUP


def _is_vuitton(name: str, name_en: str = "") -> bool:
    return any(normalize_name(x) in ("ルイビトン", "LOUISVUITTON", "ビトン")
               for x in (name, name_en) if x)


def import_course(path: str) -> dict:
    """学校のブランド一覧 {"groups":[{"group","brands":[{"name_ja","name_en","genres","note"}]}]} を取り込む。
    取り込んだブランドはオフ（ルイヴィトンだけオン）。同じ名前（表記ゆれ込み）は追加しない
    （すでにあるブランドは英字表記・ジャンルが空なら補うだけ）。結果の件数を返す"""
    with open(path, encoding="utf-8") as f:
        src = json.load(f)
    with _lock:
        data = load()
        added = merged = skipped = 0
        per_group: dict[str, int] = {}
        for grp in src.get("groups") or []:
            group = _map_group(grp.get("group"))
            for b in grp.get("brands") or []:
                if isinstance(b, str):
                    b = {"name_ja": b}
                name = str(b.get("name_ja") or b.get("name") or "").strip()
                name_en = str(b.get("name_en") or "").strip()
                if not name:
                    name = name_en
                if not name:
                    skipped += 1
                    continue
                existing = _dupe(data, name) or (_dupe(data, name_en) if name_en else None)
                genres = _clean_genres(b.get("genres") or [])
                if existing:
                    changed = False
                    if name_en and not existing.get("name_en") and normalize_name(name_en) != normalize_name(existing["name"]):
                        existing["name_en"] = name_en
                        changed = True
                    if genres and not existing.get("genres"):
                        existing["genres"] = genres
                        changed = True
                    if changed:
                        existing["updated_at"] = _now()
                        merged += 1
                    else:
                        skipped += 1
                    continue
                for t in genres:
                    if t not in free_research.GENRE_BY_KEY and t not in data["custom_genres"]:
                        data["custom_genres"].append(t)
                e = {"id": uuid.uuid4().hex[:10], "name": name,
                     "name_en": name_en if normalize_name(name_en) != normalize_name(name) else "",
                     "aliases": [], "group": group, "genres": genres, "enabled": _is_vuitton(name, name_en),
                     "memo": str(b.get("note") or "").strip(), "price_min": None, "price_max": None,
                     "min_profit": None, "origin": "course", "created_at": _now(), "updated_at": _now()}
                data["entries"].append(e)
                added += 1
                per_group[group] = per_group.get(group, 0) + 1
        save(data)
        return {"added": added, "merged": merged, "skipped": skipped, "per_group": per_group,
                "total": len(data["entries"]), "enabled": sum(1 for e in data["entries"] if e.get("enabled"))}


def import_course_new_only(src: dict) -> dict:
    """配布用（仲間の Mac）: ブランド一覧（import_course と同じ形の dict）から「まだ取り込んだことのないブランド」だけ足す。
    ・取り込んだ名前（正規化した name_ja / name_en）は my_list.json の "course_imported" に記録する。
      一度記録した名前は、仲間が削除・改名しても二度と足さない（復活させない）
    ・すでに同じブランド（表記ゆれ込み）があれば足さず、記録だけする。既存の登録は一切書き換えない（英字表記・ジャンルの補完もしない）
    ・足したブランドはオフ（ルイヴィトンだけオン）。brands_sync.py から呼ばれる"""
    with _lock:
        data = load()
        seen = set(data.get("course_imported") or [])
        added = skipped = 0
        per_group: dict[str, int] = {}
        for grp in src.get("groups") or []:
            group = _map_group(grp.get("group"))
            for b in grp.get("brands") or []:
                if isinstance(b, str):
                    b = {"name_ja": b}
                name = str(b.get("name_ja") or b.get("name") or "").strip()
                name_en = str(b.get("name_en") or "").strip()
                if not name:
                    name = name_en
                if not name:
                    continue
                keys = {k for k in (normalize_name(name), normalize_name(name_en)) if k}
                if keys & seen:
                    skipped += 1
                    continue
                seen |= keys
                if _dupe(data, name) or (_dupe(data, name_en) if name_en else None):
                    skipped += 1
                    continue
                genres = _clean_genres(b.get("genres") or [])
                for t in genres:
                    if t not in free_research.GENRE_BY_KEY and t not in data["custom_genres"]:
                        data["custom_genres"].append(t)
                data["entries"].append({
                    "id": uuid.uuid4().hex[:10], "name": name,
                    "name_en": name_en if normalize_name(name_en) != normalize_name(name) else "",
                    "aliases": [], "group": group, "genres": genres, "enabled": _is_vuitton(name, name_en),
                    "memo": str(b.get("note") or "").strip(), "price_min": None, "price_max": None,
                    "min_profit": None, "origin": "course", "created_at": _now(), "updated_at": _now()})
                added += 1
                per_group[group] = per_group.get(group, 0) + 1
        data["course_imported"] = sorted(seen)
        save(data)
        return {"added": added, "skipped": skipped, "per_group": per_group,
                "total": len(data["entries"]), "enabled": sum(1 for e in data["entries"] if e.get("enabled"))}


# ──────────────────────────────────────────────────────────────────────────────
# リサーチ（フリーリサーチの仕組みで調べる）
# ──────────────────────────────────────────────────────────────────────────────

def _all_alias_table() -> dict[str, list[str]]:
    out = {}
    for k, v in free_research.EXTRA_ALIASES.items():
        out.setdefault(k, list(v))
    for k, v in model_extractor.BRAND_ALIASES.items():
        out[k] = list(v)
    return out


def canonical_brand(entry: dict, table: dict | None = None) -> str:
    """ツールが知っているブランド名（ルイ・ヴィトン → ルイヴィトン 等）。知らなければ入力したブランド名"""
    names = [entry.get("name") or "", entry.get("name_en") or ""] + list(entry.get("aliases") or [])
    norms = {normalize_name(n) for n in names if n and len(normalize_name(n)) >= 3}
    if not norms:
        return entry.get("name") or ""
    if table is None:
        table = _all_alias_table()
    for key in table:          # まずキーそのもの
        if normalize_name(key) in norms:
            return key
    for key, aliases in table.items():
        if any(normalize_name(a) in norms for a in aliases):
            return key
    return entry.get("name") or ""


def auto_name_en(entry: dict, ignore_en: bool = False) -> str:
    """英語のつづりを自動で決める（画面では入力しない）。ツールの表（EN_QUERY / BRAND_ALIASES / EXTRA_ALIASES）から。
    分からなければ空（ブランド名そのものが英字ならそれで検索される）"""
    e = dict(entry, name_en="", aliases=[]) if ignore_en else entry
    key = canonical_brand(e)
    name = str(entry.get("name") or "")
    en = free_research.EN_QUERY.get(key, "")
    if not en:
        for a in _all_alias_table().get(key, []):
            if a and a.isascii() and re.fullmatch(r"[A-Za-z][A-Za-z0-9&.' -]{2,}", a.strip()):
                en = a
                break
    if en and normalize_name(en) == normalize_name(name):
        return ""
    return en


def _en_query_for(entry: dict) -> str:
    en = (entry.get("name_en") or "").strip()
    if en and en.isascii() and len(re.sub(r"[^A-Za-z0-9]", "", en)) >= 3:
        return en
    return ""


# 前回のリサーチで足した表記（次のリサーチの前に元に戻す。リストの編集が古い表記のまま残らないように）
_added_aliases: list[tuple[list, str]] = []
_added_extra_keys: list[str] = []
_added_en_keys: list[str] = []


def _undo_aliases() -> None:
    for lst, a in _added_aliases:
        try:
            lst.remove(a)
        except ValueError:
            pass
    _added_aliases.clear()
    for k in _added_extra_keys:
        lst = free_research.EXTRA_ALIASES.pop(k, None)
        if lst is not None and model_extractor.BRAND_ALIASES.get(k) is lst:
            model_extractor.BRAND_ALIASES.pop(k, None)
    _added_extra_keys.clear()
    for k in _added_en_keys:
        free_research.EN_QUERY.pop(k, None)
    _added_en_keys.clear()


def register_aliases(entries: list[dict]) -> None:
    """リストのブランドの英字表記・別名を、この実行中だけブランド名の照合と英字名の検索に足す（ファイルは変えない）。
    ・ブランド名の照合: model_extractor.BRAND_ALIASES（あれば）か free_research.EXTRA_ALIASES に足す
    ・英字名の検索: free_research.EN_QUERY に無ければ英字表記で足す
    英字2文字以下の表記（部分一致で他の語に当たりやすい）は照合に使わない"""
    _undo_aliases()
    for e in entries:
        key = e["_brand"]
        wanted = [e.get("name") or "", e.get("name_en") or ""] + list(e.get("aliases") or [])
        wanted = [w.strip() for w in wanted if w and w.strip()]
        wanted = [w for w in wanted if not (w.isascii() and len(re.sub(r"[^A-Za-z0-9]", "", w)) < 3)]
        if key in model_extractor.BRAND_ALIASES:
            lst = model_extractor.BRAND_ALIASES[key]
        elif key in free_research.EXTRA_ALIASES:
            lst = free_research.EXTRA_ALIASES[key]
        else:
            lst = [key]
            free_research.EXTRA_ALIASES[key] = lst
            _added_extra_keys.append(key)
        have = {unicodedata.normalize("NFKC", a).upper() for a in lst}
        for w in wanted:
            nw = unicodedata.normalize("NFKC", w).upper()
            if nw not in have:
                lst.append(w)
                have.add(nw)
                if key not in _added_extra_keys:
                    _added_aliases.append((lst, w))
        en = _en_query_for(e)
        if en and key not in free_research.EN_QUERY and normalize_name(en) != normalize_name(key):
            free_research.EN_QUERY[key] = en
            _added_en_keys.append(key)


def select_entries(data: dict, group: str = "", ids: list[str] | None = None) -> list[dict]:
    """リサーチするブランド: ids があればそのブランド（オフでも）、無ければオンのブランド（group で絞る）"""
    if ids:
        idset = set(ids)
        return [e for e in data["entries"] if e.get("id") in idset]
    return [e for e in data["entries"] if e.get("enabled") and (not group or e.get("group") == group)]


def build_list_pairs(group: str = "", ids: list[str] | None = None) -> list[dict]:
    """free_research.parse_request の list_pairs（英字表記の登録もここで行う）"""
    data = load()
    chosen = select_entries(data, group, ids)
    if not chosen:
        raise ValueError("調べるブランドがありません（マイブランド登録でブランドをオンにしてください）")
    out, merged = [], {}
    prepared = []
    for e in chosen:
        e = dict(e, _brand=canonical_brand(e))
        prepared.append(e)
        genres, _ = effective_genres(e, data)
        terms = entry_terms(e)
        if terms:   # 型番・モデル名で調べるときのジャンルは、自分で選んだものだけ（別アイテムの除外等に使う）
            genres = list(e.get("genres") or [])
        pmin, pmax, per = e.get("price_min") or None, e.get("price_max") or None, e.get("per_source") or None
        # 表記ちがいで同じブランドが2件あるときは、型番・モデル名・金額・検索数が同じなら1件にまとめる
        key = (e["_brand"], tuple(terms), pmin, pmax, per)
        if key in merged:
            for g in genres:
                if g not in merged[key]["genres"]:
                    merged[key]["genres"].append(g)
            continue
        # 下限・上限金額はブランドの指定を優先（空欄ならリサーチ画面の範囲）。最低利益はリサーチ画面の条件
        lp = {"brand": e["_brand"], "genres": list(genres), "price_min": pmin, "price_max": pmax,
              "min_profit": None, "terms": terms, "per_source": per, "entry_id": e.get("id")}
        merged[key] = lp
        out.append(lp)
    register_aliases(prepared)
    return [lp for lp in out if lp["genres"] or lp["terms"]]


def to_free_request(req: dict) -> dict:
    """画面の「リストでリサーチ」の条件 → フリーリサーチの条件（mode=free ＋ list_pairs）"""
    ids = req.get("list_ids") or None
    pairs = build_list_pairs(str(req.get("list_group") or ""), ids)
    out = dict(req)
    out["mode"] = "free"
    out["list_pairs"] = pairs
    out["free_brands"] = [p["brand"] for p in pairs]
    out["free_genres"] = []
    out["free_custom_genre"] = ""
    return out
