"""
モデル登録（写真と相場を自分で登録しておき、写真の見た目が同じ出品だけを探す）

「ブランド × ジャンル」の全体相場では、同じブランドのバッグでもモデルごとに値段が大きく違い、現実に合わない。
ここでは自分で モデルごとに「写真（1〜数枚）」と「相場（円）」を登録しておき、リサーチでは
  1) ブランド名（＋検索する言葉）で仕入れ先を検索
  2) 出品の1枚目の写真と、登録した写真の「似ている度」を計算（Mac 内蔵の画像認識 = Apple Vision。
     VNGenerateForegroundInstanceMaskRequest で背景を消して商品だけにしてから、
     VNGenerateImageFeaturePrintRequest で特徴量を出す。無料・ログインなし・外部の API は使わない）
  3) 似ている出品だけ、登録した相場で利益を計算（似ていない出品は結果に出さない）
を行う。

・保存先: model_registry.json（登録内容）と model_photos/（登録した写真。JPEG に直して保存）。どちらも共有しない。
・似ている度（0〜1）: settings.model_match_strong（既定 0.80）以上 = 一致（確かさ 高）、
  settings.model_match_weak（既定 0.74）以上 = 似ている・要確認（確かさ 中）、それ未満は出さない。
  2026-11-26 にサンローランのバッグ（クラッチ・トート）の出品17件で確認: 背景を消さないと、同じ出品者の
  「白背景＋ロゴ文字」の撮り方が似ているだけで別モデル（リボン付きトート）が 0.85 になった。背景を消すと
  別モデルは 0.56〜0.77 に下がり、同じモデルは 0.76〜0.97（10件中8件が 0.80 以上）。
  それでも同じ柄・同じ色の形違いは 0.80 を超えることがあるので、結果のカードに登録写真を並べて目で確認できるようにしている。
・出品の写真の特徴量は DB の model_fp_cache に写真URLごとに保存する（同じ写真は取り直さない）。
・使えるのは macOS で pyobjc-framework-Vision が入っているときだけ（ocr_reader と同じ条件）。
・写真の似ている度は「全体の見た目」しか見ないので、同じ色・形のダウン等は別モデルでも「似ている」になる。
  そこで登録に任意の「型番」（複数可）と「ファスナーの色」（黒 / 金属色）を持たせ、写真が似ている候補の上位だけを確かめる（_verify）:
    型番: タイトル → 説明文 → 写真の文字（ocr_reader の Apple Vision OCR。写真は最大6枚 = 先頭2枚＋タグが写りがちな後ろ4枚）。
      登録した型番があれば「型番一致」で確かさ 高（ほかの確認より優先）。このブランドの型番の形の別の型番だけが読めたら除外
      （タトラス等で品番→モデル名の表 model_extractor.number_name が登録のモデル名と同じなら一致扱い）。読めなければほかの確認に任せる。
    ファスナー: Mac 内 AI（Ollama の qwen3-vl。local_inspector.chat）に出品の写真（先頭2枚）を見せて 黒 / 金属 / 不明。
      登録と違えば除外、不明は残して「要確認」。Ollama が使えなければ確認を省いて今までどおり残す。
  2026-11-27 の試し（タトラスのダウン 31枚を目視と答え合わせ）: ファスナーの色ははっきり写った写真ならほぼ正解（黒を金属と誤答 0件。
  隠れたファスナーを「黒」と答える誤りが3件）。ポケットの角度（縦 / ハの字 / 横）は指示の書き方で答えが大きく変わり（正解 5〜8割・
  横のフラップを縦と誤答）、横の仕切りの数も同じ写真で 5本→7本 と揺れたため採用していない。
  判定は写真URLごとに DB の model_attr_cache、型番用の OCR は photo_ocr_cache（出品URLの頭に "model:" を付けて通常の OCR と分ける）に保存。
"""

import base64
import io
import json
import math
import os
import re
import struct
import sys
import threading
import time
import uuid
from datetime import datetime

import database
from models import Deal, MarketPrice

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REG_PATH = os.path.join(BASE_DIR, "model_registry.json")
PHOTO_DIR = os.path.join(BASE_DIR, "model_photos")

# 特徴量の出し方（前処理・Vision の版）を変えたら上げる（登録写真は計算し直し、出品写真のキャッシュは別になる）
FP_VERSION = "2"
_FP_EDGE = 512            # 特徴量を出す前に長辺をこの大きさまで縮める
_MASK_EDGE = 768          # 背景を消すときの長辺
_SAVE_EDGE = 1024         # 登録写真を保存するときの長辺
MAX_PHOTOS = 8            # 1モデルに登録できる写真の枚数
_DOWNLOAD_INTERVAL = 0.2  # 出品写真のダウンロード間隔（秒）
_CACHE_TABLE = "model_fp_cache"

DEFAULTS = {"model_match_strong": 0.80, "model_match_weak": 0.74}

# ── 型番・ファスナーの確認（_verify） ──
ZIPPER_CHOICES = ("黒", "金属")   # 画面では「黒」「金属色」。"" = 指定なし
MAX_NUMBERS = 12
_ATTR_TABLE = "model_attr_cache"
_ATTR_VERSION = "1"        # ファスナーの判定の指示・縮小を変えたら上げる
_CHECK_TOP = 20            # 1組の検索で型番・ファスナーを確かめる候補の上限（似ている度の高い順）
_OCR_PHOTOS = 6            # 型番を読む写真の上限（先頭2枚＋後ろ4枚）
_ZIP_PHOTOS = 2            # ファスナーを見る写真の枚数（先頭から）
_ZIP_EDGE = 768
_ZIP_TIMEOUT = 60
_ZIP_SCHEMA = {"type": "object", "properties": {"zipper": {"type": "string", "enum": ["black", "metal", "unknown"]}},
               "required": ["zipper"]}
# 2026-11-27 の試しでいちばん当たった指示（英語のほうが安定。分からなければ unknown と言わせる）
_ZIP_PROMPT = """You inspect a photo of a jacket for a resale shop. Look only at the main center front zipper (the long zipper that closes the jacket).
"black": its teeth look black or dark (plastic, coated or matte dark metal).
"metal": its teeth are shiny silver or gold metal.
"unknown": the main zipper is not visible in this photo (back view, hidden under a flap or snaps, close-up of another part) or you are not sure."""

# 写真が同じでも仕入れてはいけない出品（偽物・箱だけ・まとめ売り等）。ノベルティは登録するモデルのことがあるので除かない
_BAD_RE = re.compile(r"偽物|偽造|レプリカ|コピー品|スーパーコピー|空箱|箱のみ|袋のみ|保存袋のみ|タグのみ|パーツのみ|"
                     r"まとめ売り|\d+\s*点セット|\d+\s*点まとめ|写真のみ|画像のみ|ジャンク")

_lock = threading.RLock()
_vision = {"checked": False, "ok": False}
stats = {"searched": 0, "titled": 0, "compared": 0, "cached": 0, "downloaded": 0, "noimg": 0,
         "strong": 0, "weak": 0, "seconds": 0.0,
         "num_match": 0, "num_excluded": 0, "zip_ok": 0, "zip_excluded": 0, "zip_unknown": 0, "zip_skipped": 0}


# ──────────────────────────────────────────────────────────────────────────────
# 写真の特徴量（Apple Vision）
# ──────────────────────────────────────────────────────────────────────────────

def available() -> bool:
    """この Mac で写真の照合を使えるか"""
    if not _vision["checked"]:
        _vision["checked"] = True
        if sys.platform == "darwin":
            try:
                import Vision      # noqa: F401
                import Foundation  # noqa: F401
                _vision["ok"] = True
            except Exception:
                pass
    return _vision["ok"]


def _to_jpeg(data: bytes, edge: int) -> bytes | None:
    """写真を RGB の JPEG（長辺 edge 以下・向きは EXIF どおり）に直す。読めなければ None"""
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            # 透過は白地に（商品写真の切り抜き画像）
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        img.thumbnail((edge, edge), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return None


def _foreground(jpeg: bytes) -> bytes | None:
    """背景を消して商品だけにした写真（白地・商品の範囲で切り抜き）の JPEG。
    商品を見つけられない・ほぼ全面が商品（接写）・小さすぎるときは None（元の写真のまま比べる）"""
    import objc
    import Foundation
    import Quartz
    import Vision
    from PIL import Image
    if not hasattr(Vision, "VNGenerateForegroundInstanceMaskRequest"):   # macOS 14 より前
        return None
    try:
        with objc.autorelease_pool():
            ns = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns, None)
            rq = Vision.VNGenerateForegroundInstanceMaskRequest.alloc().init()
            ok, _err = handler.performRequests_error_([rq], None)
            if not ok or not rq.results():
                return None
            obs = rq.results()[0]
            buf, _err = obs.generateScaledMaskForImageForInstances_fromRequestHandler_error_(
                obs.allInstances(), handler, None)
            if buf is None or Quartz.CVPixelBufferGetPixelFormatType(buf) != 0x4C303066:   # 'L00f' = 32bit float 1ch
                return None
            Quartz.CVPixelBufferLockBaseAddress(buf, 1)
            try:
                w, h = Quartz.CVPixelBufferGetWidth(buf), Quartz.CVPixelBufferGetHeight(buf)
                bpr = Quartz.CVPixelBufferGetBytesPerRow(buf)
                raw = bytes(Quartz.CVPixelBufferGetBaseAddress(buf).as_buffer(bpr * h))
            finally:
                Quartz.CVPixelBufferUnlockBaseAddress(buf, 1)
        mask = Image.frombuffer("F", (w, h), raw, "raw", "F", bpr, 1)
        mask = mask.point(lambda x: x * 255).convert("L").point(lambda x: 255 if x > 127 else 0)
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        if mask.size != img.size:
            mask = mask.resize(img.size)
        box = mask.getbbox()
        if not box:
            return None
        area = mask.histogram()[255] / (img.size[0] * img.size[1])
        if area < 0.03 or area > 0.97:
            return None
        out = Image.new("RGB", img.size, "white")
        out.paste(img, mask=mask)
        pad = int(0.04 * max(box[2] - box[0], box[3] - box[1]))
        out = out.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                        min(img.size[0], box[2] + pad), min(img.size[1], box[3] + pad)))
        out.thumbnail((_FP_EDGE, _FP_EDGE), Image.LANCZOS)
        b = io.BytesIO()
        out.save(b, format="JPEG", quality=90)
        return b.getvalue()
    except Exception:
        return None


def fingerprint(data: bytes) -> list[float] | None:
    """写真のバイト列 → 特徴量（長さ1に正規化したベクトル）。失敗したら None。
    背景（床・壁・出品者ごとの白背景やロゴ文字）で似てしまわないよう、商品だけにしてから特徴量を出す"""
    if not available():
        return None
    jpeg = _to_jpeg(data, _MASK_EDGE)
    if jpeg is None:
        return None
    jpeg = _foreground(jpeg) or _to_jpeg(jpeg, _FP_EDGE)
    if jpeg is None:
        return None
    import objc
    import Foundation
    import Vision
    try:
        with objc.autorelease_pool():
            ns = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns, None)
            rq = Vision.VNGenerateImageFeaturePrintRequest.alloc().init()
            ok, _err = handler.performRequests_error_([rq], None)
            if not ok or not rq.results():
                return None
            obs = rq.results()[0]
            n = int(obs.elementCount())
            raw = bytes(obs.data())
            if n <= 0 or len(raw) < 4 * n:
                return None
            v = struct.unpack(f"{n}f", raw[:4 * n])
    except Exception:
        return None
    norm = math.sqrt(sum(x * x for x in v))
    if not norm:
        return None
    return [x / norm for x in v]


def _pack(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _unpack(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


def similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ──────────────────────────────────────────────────────────────────────────────
# 登録（model_registry.json / model_photos/）
# ──────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load() -> dict:
    with _lock:
        try:
            with open(REG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        except Exception:
            # 壊れていたら上書きせず別名で残す
            try:
                os.replace(REG_PATH, REG_PATH + ".broken-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            except OSError:
                pass
            data = {}
        data.setdefault("version", 1)
        data.setdefault("entries", [])
        return data


def save(data: dict) -> None:
    with _lock:
        tmp = REG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, REG_PATH)


def _find(data: dict, entry_id: str) -> dict:
    for e in data["entries"]:
        if e["id"] == entry_id:
            return e
    raise KeyError("そのモデルは見つかりません（一覧を読み込み直してください）")


def _price(v) -> int:
    try:
        n = int(float(str(v).replace(",", "").replace("円", "").replace("¥", "").strip()))
    except (TypeError, ValueError):
        raise ValueError("相場は数字（円）で入れてください")
    if n < 500:
        raise ValueError("相場は500円以上で入れてください")
    if n > 10_000_000:
        raise ValueError("相場が大きすぎます（1,000万円まで）")
    return n


def _text(v, label: str, required: bool, limit: int = 60) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    if required and not s:
        raise ValueError(f"{label}を入れてください")
    if len(s) > limit:
        raise ValueError(f"{label}は{limit}文字までにしてください")
    return s


def _zipper(v) -> str:
    s = str(v or "").strip()
    s = {"指定なし": "", "金属色": "金属", "none": "", "black": "黒", "metal": "金属"}.get(s, s)
    if s and s not in ZIPPER_CHOICES:
        raise ValueError("ファスナーの色は「指定なし」「黒」「金属色」から選んでください")
    return s


def _numbers(v) -> list[str]:
    """型番の欄（カンマ・読点・空白区切り、または一覧）→ 型番の一覧（大文字・重複なし）"""
    import unicodedata
    parts = v if isinstance(v, list) else re.split(r"[,、，;／/\s]+", str(v or ""))
    out = []
    for p in parts:
        t = unicodedata.normalize("NFKC", str(p or "")).strip().upper()
        if not t:
            continue
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-.]{2,29}", t) or not re.search(r"\d", t):
            raise ValueError(f"型番「{t[:30]}」は英数字（とハイフン）で入れてください（複数はカンマ区切り）")
        if t not in out:
            out.append(t)
    if len(out) > MAX_NUMBERS:
        raise ValueError(f"型番は{MAX_NUMBERS}個までにしてください")
    return out


def norm_number(s: str) -> str:
    """型番の照合用の形: 大文字・英数字だけ・読み間違いやすい O→0、I・L→1 をそろえる（両側に同じ変換をかけて比べる）"""
    import unicodedata
    t = re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKC", str(s or "")).upper())
    return t.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def _add_photo(entry: dict, raw: bytes) -> None:
    if len(entry["photos"]) >= MAX_PHOTOS:
        raise ValueError(f"写真は1モデル{MAX_PHOTOS}枚までです")
    jpeg = _to_jpeg(raw, _SAVE_EDGE)
    if jpeg is None:
        raise ValueError("写真を読み込めませんでした（JPEG・PNG・WebP などの画像ファイルを選んでください）")
    fp = fingerprint(jpeg)
    if fp is None:
        raise ValueError("写真の特徴を読み取れませんでした" if available()
                         else "この Mac では写真の照合が使えません（macOS と Vision の部品が必要です）")
    os.makedirs(PHOTO_DIR, exist_ok=True)
    name = f"{entry['id']}_{uuid.uuid4().hex[:8]}.jpg"
    with open(os.path.join(PHOTO_DIR, name), "wb") as f:
        f.write(jpeg)
    entry["photos"].append({"file": name, "fp": base64.b64encode(_pack(fp)).decode("ascii"), "fp_version": FP_VERSION})


def add_entry(fields: dict, photos: list[bytes]) -> dict:
    """モデルを登録する。fields: brand / name / query / price。photos: 写真のバイト列（1枚以上）"""
    if not photos:
        raise ValueError("写真を1枚以上選んでください")
    entry = {
        "id": uuid.uuid4().hex[:10],
        "brand": _text(fields.get("brand"), "ブランド", True),
        "name": _text(fields.get("name"), "モデル名", True),
        "query": _text(fields.get("query"), "検索する言葉", False),
        "price": _price(fields.get("price")),
        "numbers": _numbers(fields.get("numbers")),
        "zipper": "" if fields.get("zipper") == "auto" else _zipper(fields.get("zipper")),
        "enabled": True,
        "photos": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        for raw in photos:
            _add_photo(entry, raw)
    except Exception:
        _remove_files(entry)
        raise
    # 「写真から自動」: 登録写真からファスナーの色（Mac 内 AI）を、型番が空なら写真の文字（OCR）から型番を入れる
    auto = fields.get("zipper") == "auto"
    if auto or not entry["numbers"]:
        try:
            sug = suggest(photos, _brand_key(entry["brand"]), zipper=auto)
            if auto:
                entry["zipper"] = sug["zipper"]
                entry["zipper_auto"] = True
            if not entry["numbers"]:
                entry["numbers"] = sug["numbers"]
        except Exception as ex:
            print(f"  [モデル登録] 写真からの自動入力を省きました: {type(ex).__name__}")
    with _lock:
        data = load()
        data["entries"].append(entry)
        save(data)
    return _public(entry)


def update_entry(entry_id: str, fields: dict) -> dict:
    with _lock:
        data = load()
        e = _find(data, entry_id)
        if "brand" in fields:
            e["brand"] = _text(fields["brand"], "ブランド", True)
        if "name" in fields:
            e["name"] = _text(fields["name"], "モデル名", True)
        if "query" in fields:
            e["query"] = _text(fields["query"], "検索する言葉", False)
        if "price" in fields:
            e["price"] = _price(fields["price"])
        if "numbers" in fields:
            e["numbers"] = _numbers(fields["numbers"])
        if "zipper" in fields:
            z = _zipper(fields["zipper"])
            if z != e.get("zipper", ""):
                e.pop("zipper_auto", None)
            e["zipper"] = z
        if "enabled" in fields:
            e["enabled"] = bool(fields["enabled"])
        e["updated_at"] = _now()
        save(data)
        return _public(e)


def add_photos(entry_id: str, photos: list[bytes]) -> dict:
    if not photos:
        raise ValueError("写真を選んでください")
    with _lock:
        data = load()
        e = _find(data, entry_id)
        before = list(e["photos"])
        try:
            for raw in photos:
                _add_photo(e, raw)
        except Exception:
            for p in e["photos"][len(before):]:
                _unlink(p["file"])
            raise
        e["updated_at"] = _now()
        save(data)
        return _public(e)


def delete_photo(entry_id: str, file: str) -> dict:
    with _lock:
        data = load()
        e = _find(data, entry_id)
        if len(e["photos"]) <= 1:
            raise ValueError("写真は1枚以上必要です（モデルごと消すときは「削除」を使ってください）")
        keep = [p for p in e["photos"] if p["file"] != file]
        if len(keep) == len(e["photos"]):
            raise KeyError("その写真は見つかりません")
        e["photos"] = keep
        e["updated_at"] = _now()
        save(data)
        _unlink(file)
        return _public(e)


def delete_entry(entry_id: str) -> None:
    with _lock:
        data = load()
        e = _find(data, entry_id)
        data["entries"] = [x for x in data["entries"] if x["id"] != entry_id]
        save(data)
        _remove_files(e)


def set_all_enabled(on: bool) -> None:
    with _lock:
        data = load()
        for e in data["entries"]:
            e["enabled"] = bool(on)
        save(data)


def _unlink(file: str) -> None:
    try:
        os.remove(os.path.join(PHOTO_DIR, os.path.basename(file)))
    except OSError:
        pass


def _remove_files(entry: dict) -> None:
    for p in entry.get("photos", []):
        _unlink(p["file"])


def _public(e: dict) -> dict:
    """画面に返す形（特徴量は返さない）"""
    return {"id": e["id"], "brand": e["brand"], "name": e["name"], "query": e.get("query", ""),
            "price": e["price"], "enabled": bool(e.get("enabled", True)),
            "numbers": list(e.get("numbers") or []), "zipper": e.get("zipper", ""),
            "zipper_auto": bool(e.get("zipper_auto")),
            "photos": [p["file"] for p in e.get("photos", [])]}


def view() -> dict:
    data = load()
    return {"available": available(), "max_photos": MAX_PHOTOS,
            "strong": DEFAULTS["model_match_strong"], "weak": DEFAULTS["model_match_weak"],
            "entries": [_public(e) for e in data["entries"]]}


def photo_path(file: str) -> str | None:
    name = os.path.basename(file or "")
    if not re.fullmatch(r"[0-9a-f]{10}_[0-9a-f]{8}\.jpg", name):
        return None
    path = os.path.join(PHOTO_DIR, name)
    return path if os.path.isfile(path) else None


def select_entries(ids: list[str] | None = None) -> list[dict]:
    """リサーチするモデル: ids があればそのモデル（オフでも）、無ければオンのモデル"""
    entries = load()["entries"]
    if ids:
        want = set(ids)
        return [e for e in entries if e["id"] in want and e.get("photos")]
    return [e for e in entries if e.get("enabled", True) and e.get("photos")]


# ── メルカリの商品ページから写真を取り込む（登録画面の「メルカリのURLから」） ─────────────────
_MERCARI_ITEM_RE = re.compile(r"^https://jp\.mercari\.com/(?:[a-z]{2}(?:-[A-Za-z]{2})?/)?item/(m\d{6,})")
_MERCARI_PHOTO_RE = re.compile(r"^https://static\.mercdn\.net/item/detail/[\w/.\-]+\.(?:jpe?g|png|webp)(?:\?[\w=&.\-]*)?$")


def fetch_mercari(url: str, brand: str = "") -> dict:
    """メルカリの商品ページ（出品中・売り切れ）の写真URL・商品名・値段。ログインなし。
    brand があれば商品名と写真の文字（OCR。先頭2枚＋後ろ6枚）から型番の候補も出す（numbers）。
    戻り値 {url, title, price, sold, images, numbers}"""
    m = _MERCARI_ITEM_RE.match(str(url or "").strip())
    if not m:
        raise ValueError("メルカリの商品ページのURL（https://jp.mercari.com/item/m… ）を貼ってください")
    item_url = f"https://jp.mercari.com/item/{m.group(1)}"
    from playwright.sync_api import sync_playwright
    from scrapers import mercari as mercari_scraper
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = mercari_scraper.new_page(browser)
            images = mercari_scraper.get_image_urls(page, [item_url]).get(item_url) or []
            info = page.evaluate("""() => {
                const t = s => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; };
                return { title: t('h1'), price: t('[data-testid="price"]'),
                         sold: !!document.querySelector('[data-testid="thumbnail-sticker"], [aria-label="売り切れ"]')
                               || /売り切れました/.test(document.body.innerText.slice(0, 4000)) };
            }""") if images else {}
        finally:
            browser.close()
    images = [u for u in images if _MERCARI_PHOTO_RE.match(u)][:20]
    if not images:
        raise ValueError("写真を取れませんでした（削除された商品か、URLが違います）")
    digits = re.sub(r"[^\d]", "", info.get("price") or "")
    title = (info.get("title") or "")[:120]
    numbers = []
    brand = _brand_key(brand)
    if brand:
        try:
            import ocr_reader
            numbers = numbers_in_text(title, brand)
            if ocr_reader.available():
                got = ocr_reader.ocr_photos(list(dict.fromkeys(images[:2] + images[-6:])))
                for n in numbers_in_text(got[0] if got else "", brand):
                    if n not in numbers:
                        numbers.append(n)
        except Exception as ex:
            print(f"  [モデル登録] 型番の読み取りを省きました: {type(ex).__name__}")
    return {"url": item_url, "title": title,
            "price": int(digits) if digits and len(digits) <= 8 else None,
            "sold": bool(info.get("sold")), "images": images, "numbers": numbers[:MAX_NUMBERS]}


def _brand_key(name: str) -> str:
    """画面に入れたブランド名 → model_extractor のブランド名（タトラス等）。分からなければ入れたまま"""
    name = str(name or "").strip()
    if not name:
        return ""
    try:
        import my_list
        return my_list.canonical_brand({"name": name, "name_en": "", "aliases": []}) or name
    except Exception:
        return name


def download_mercari_photos(urls: list[str]) -> list[bytes]:
    """fetch_mercari で出した写真URL（メルカリの画像サーバーだけ）をダウンロードする"""
    import photo_reader
    out = []
    for u in urls[:MAX_PHOTOS]:
        if not _MERCARI_PHOTO_RE.match(u or ""):
            raise ValueError("メルカリの写真以外のURLは取り込めません")
        raw = photo_reader.download_bytes(u)
        if raw is None:
            raise ValueError("メルカリの写真を取れませんでした。もう一度試してください")
        out.append(raw)
    return out


def _entry_vectors(e: dict) -> list[tuple[str, list[float]]]:
    """登録写真の特徴量 [(ファイル名, ベクトル)]。版が古い・無いものは写真から計算し直す（保存はしない）"""
    out = []
    for p in e.get("photos", []):
        v = None
        if p.get("fp") and p.get("fp_version") == FP_VERSION:
            try:
                v = _unpack(base64.b64decode(p["fp"]))
            except Exception:
                v = None
        if v is None:
            path = photo_path(p["file"])
            if path:
                with open(path, "rb") as f:
                    v = fingerprint(f.read())
                if v is not None:
                    _store_vector(e["id"], p["file"], v)
        if v is not None:
            out.append((p["file"], v))
    return out


def _store_vector(entry_id: str, file: str, v: list[float]) -> None:
    """計算し直した登録写真の特徴量を保存する（次からは計算しない）"""
    with _lock:
        data = load()
        for e in data["entries"]:
            if e["id"] != entry_id:
                continue
            for p in e.get("photos", []):
                if p["file"] == file:
                    p["fp"] = base64.b64encode(_pack(v)).decode("ascii")
                    p["fp_version"] = FP_VERSION
                    save(data)
                    return


# ──────────────────────────────────────────────────────────────────────────────
# 出品写真の特徴量（DB に保存）
# ──────────────────────────────────────────────────────────────────────────────

def _cache_init() -> None:
    with database._conn() as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {_CACHE_TABLE} (
            image_url TEXT NOT NULL, version TEXT NOT NULL, fp BLOB NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (image_url, version))""")


def _cache_get(urls: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    with database._conn() as conn:
        for i in range(0, len(urls), 400):
            chunk = urls[i:i + 400]
            rows = conn.execute(
                f"SELECT image_url, fp FROM {_CACHE_TABLE} WHERE version = ? AND image_url IN ({','.join('?' * len(chunk))})",
                [FP_VERSION] + chunk).fetchall()
            for r in rows:
                out[r["image_url"]] = _unpack(r["fp"])
    return out


def _cache_put(url: str, v: list[float]) -> None:
    with database._conn() as conn:
        conn.execute(f"INSERT OR REPLACE INTO {_CACHE_TABLE} (image_url, version, fp, created_at) VALUES (?, ?, ?, ?)",
                     (url, FP_VERSION, _pack(v), _now()))


def _item_vectors(items: list, should_stop=None, on_step=None) -> dict[str, list[float]]:
    """出品の1枚目の写真の特徴量 {出品URL: ベクトル}（写真が無い・取れない出品は含まない）"""
    import photo_reader
    with_img = [it for it in items if it.image_url]
    stats["noimg"] += len(items) - len(with_img)
    cached = _cache_get(list({it.image_url for it in with_img}))
    out: dict[str, list[float]] = {}
    done = 0
    for it in with_img:
        if should_stop and should_stop():
            break
        done += 1
        v = cached.get(it.image_url)
        if v is not None:
            stats["cached"] += 1
        else:
            if on_step:
                on_step(done, len(with_img))
            raw = photo_reader.download_bytes(it.image_url)
            time.sleep(_DOWNLOAD_INTERVAL)
            v = fingerprint(raw) if raw else None
            if v is None:
                stats["noimg"] += 1
                continue
            stats["downloaded"] += 1
            cached[it.image_url] = v
            _cache_put(it.image_url, v)
        out[it.url] = v
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 型番・ファスナーの色（登録時の自動入力と、照合後の確認）
# ──────────────────────────────────────────────────────────────────────────────

def _attr_init() -> None:
    with database._conn() as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {_ATTR_TABLE} (
            image_url TEXT NOT NULL, attr TEXT NOT NULL, version TEXT NOT NULL, value TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY (image_url, attr, version))""")


def _attr_get(url: str, attr: str) -> str | None:
    with database._conn() as conn:
        r = conn.execute(f"SELECT value FROM {_ATTR_TABLE} WHERE image_url = ? AND attr = ? AND version = ?",
                         (url, attr, _ATTR_VERSION)).fetchone()
    return r["value"] if r else None


def _attr_put(url: str, attr: str, value: str) -> None:
    with database._conn() as conn:
        conn.execute(f"INSERT OR REPLACE INTO {_ATTR_TABLE} (image_url, attr, version, value, created_at) VALUES (?, ?, ?, ?, ?)",
                     (url, attr, _ATTR_VERSION, value, _now()))


def ai_ready() -> tuple[bool, str]:
    """ファスナーの色を判定する Mac 内 AI（Ollama）が使えるか。止まっていれば起動を試みる"""
    import local_inspector
    if not local_inspector.installed():
        return False, "Ollama が入っていません"
    return local_inspector.check_server(start=True)


def zipper_of_photo(raw: bytes) -> str | None:
    """写真1枚のファスナーの色: "黒" / "金属" / "不明"。AI を呼べなければ None"""
    import local_inspector
    b64 = local_inspector.prepare_image(raw, _ZIP_EDGE)
    if not b64:
        return "不明"
    out = local_inspector.chat("Answer for this photo.", [b64], _ZIP_SCHEMA, system=_ZIP_PROMPT, timeout=_ZIP_TIMEOUT)
    if not out:
        return None
    return {"black": "黒", "metal": "金属"}.get(str(out.get("zipper")), "不明")


def _combine_zipper(vals: list[str]) -> str:
    """写真ごとの答え → 1つに（黒と金属が割れたら不明）"""
    got = {v for v in vals if v in ZIPPER_CHOICES}
    return got.pop() if len(got) == 1 else "不明"


def numbers_in_text(text: str, brand: str) -> list[str]:
    """文字の中の「このブランドの型番の形」の型番（model_extractor の正規の形。1行ずつ・OCR の読み間違いを直した行でも）"""
    import model_extractor
    import ocr_reader
    out = []
    for ln in str(text or "").splitlines():
        if not ln.strip():
            continue
        for v in dict.fromkeys([ln, ocr_reader._fix_line(ln, brand)]):
            m = model_extractor.extract(v, brand)
            if model_extractor.is_number(m, brand) and m not in out:
                out.append(m)
    return out


def suggest(photos: list[bytes], brand: str, zipper: bool = True) -> dict:
    """登録写真からの自動入力: {"zipper": "黒"/"金属"/"", "numbers": [...], "note": str}
    ファスナーは Mac 内 AI（先頭4枚まで）、型番は写真の文字（Apple Vision の OCR）から"""
    import ocr_reader
    res = {"zipper": "", "numbers": [], "note": ""}
    if ocr_reader.available():
        for raw in photos[:MAX_PHOTOS]:
            for n in numbers_in_text("\n".join(ocr_reader.ocr_image(raw)), brand):
                if n not in res["numbers"]:
                    res["numbers"].append(n)
    if zipper:
        ok, reason = ai_ready()
        if not ok:
            res["note"] = f"ファスナーの色は自動で入れられませんでした（{reason}）"
        else:
            vals = [v for v in (zipper_of_photo(r) for r in photos[:4]) if v]
            z = _combine_zipper(vals) if vals else "不明"
            res["zipper"] = z if z in ZIPPER_CHOICES else ""
            if not res["zipper"]:
                res["note"] = "ファスナーの色は写真から分かりませんでした（必要なら自分で選んでください）"
    return res


def suggest_entry(entry_id: str) -> dict:
    """登録済みのモデルの写真から自動入力の候補を出す（保存はしない。編集画面の「写真から自動で入れる」）"""
    e = _find(load(), entry_id)
    raws = []
    for p in e.get("photos", []):
        path = photo_path(p["file"])
        if path:
            with open(path, "rb") as f:
                raws.append(f.read())
    return suggest(raws, _brand_key(e["brand"]))


def _number_check(text: str, brand: str, entries: list[dict]) -> tuple[str, dict | None, str]:
    """文字（タイトル・説明文・写真の文字）の型番を登録と比べる。
    戻り値 ("match", 一致した登録, 型番) / ("other", None, 読めた別の型番) / ("", None, "")"""
    import model_extractor
    if not text:
        return "", None, ""
    flat = norm_number(text)
    for e in entries:
        for n in e.get("numbers") or []:
            k = norm_number(n)
            if len(k) >= 5 and k in flat:
                return "match", e, n
    found = numbers_in_text(text, brand)
    for n in found:
        # 品番→モデル名の表（タトラス・モンクレール・カナダグース）で登録のモデル名と同じなら一致扱い（年違いの品番等）
        name = model_extractor.number_name(n, brand)
        if name:
            for e in entries:
                if e.get("numbers") and name.replace(" ", "") in e["name"].replace(" ", ""):
                    return "match", e, n
    if found:
        return "other", None, found[0]
    return "", None, ""


def _ocr_photo_urls(urls: list[str]) -> list[str]:
    """型番を読む写真: 先頭2枚＋後ろ4枚（タグ・品質表示は後ろの方に載せる人が多い）"""
    pick = list(dict.fromkeys(urls[:2] + urls[-(_OCR_PHOTOS - 2):]))
    return pick[:_OCR_PHOTOS]


def _ocr_text(item_url: str, urls: list[str]) -> str | None:
    import ocr_reader
    pick = _ocr_photo_urls(urls)
    key_url = "model:" + item_url
    key = ocr_reader.photos_key(pick)
    hit = database.get_cached_photo_ocr([(key_url, key)], ocr_reader.OCR_CACHE_HOURS)
    if key_url in hit:
        return hit[key_url]
    got = ocr_reader.ocr_photos(pick)
    if got is None:
        return None
    database.cache_photo_ocr(key_url, key, got[0], None, ocr_reader.OCR_CACHE_HOURS)
    return got[0]


def _zipper_of_item(it, urls: list[str]) -> str | None:
    """出品のファスナーの色（先頭 _ZIP_PHOTOS 枚。写真URLごとに保存）。AI を呼べなければ None"""
    import photo_reader
    vals = []
    for u in (urls or [it.image_url])[:_ZIP_PHOTOS]:
        if not u:
            continue
        v = _attr_get(u, "zipper")
        if v is None:
            raw = photo_reader.download_bytes(u)
            time.sleep(_DOWNLOAD_INTERVAL)
            if raw is None:
                continue
            v = zipper_of_photo(raw)
            if v is None:
                return None
            _attr_put(u, "zipper", v)
        vals.append(v)
    return _combine_zipper(vals) if vals else "不明"


def _verify(cands: list[dict], g: dict, page, scan_main, progress_note=None, should_stop=None) -> list[dict]:
    """写真が似ている候補を、登録の型番・ファスナーの色で確かめる。残す候補を返す（cand に "checks" を足す）。
    cand: {"item", "entry", "sim", "level"}"""
    import ocr_reader
    brand, entries = g["brand"], g["entries"]
    num_entries = [e for e in entries if e.get("numbers")]
    if not num_entries and not any(c["entry"].get("zipper") for c in cands):
        return cands
    cands.sort(key=lambda c: c["sim"], reverse=True)
    top, rest = cands[:_CHECK_TOP], cands[_CHECK_TOP:]
    for c in rest:
        c["checks"] = [{"text": "候補が多いため型番・ファスナーは未確認", "ok": False}]
    for c in top:
        c["checks"] = []
        c["num"] = ""
    items = [c["item"] for c in top]

    # 1) 型番: タイトル → 説明文 → 写真の文字
    image_urls: dict = {}
    if num_entries:
        todo = []
        for c in top:
            st, e, n = _number_check(c["item"].title, brand, num_entries)
            c["num"], c["num_entry"], c["num_value"], c["num_from"] = st, e, n, "タイトル"
            if not st:
                todo.append(c)
        if todo and not (should_stop and should_stop()):
            if progress_note:
                progress_note(f"型番を確認中（説明文 {len(todo)}件）")
            descs = scan_main.fetch_descriptions([c["item"] for c in todo], page, should_stop)
            for c in todo:
                st, e, n = _number_check(descs.get(c["item"].url, ""), brand, num_entries)
                c["num"], c["num_entry"], c["num_value"], c["num_from"] = st, e, n, "説明文"
            todo = [c for c in todo if not c["num"]]
        if todo and ocr_reader.available() and not (should_stop and should_stop()):
            if progress_note:
                progress_note(f"型番を確認中（写真の文字 {len(todo)}件）")
            image_urls.update(scan_main._get_image_urls([c["item"] for c in todo], page))
            for c in todo:
                if should_stop and should_stop():
                    break
                urls = image_urls.get(c["item"].url) or []
                text = _ocr_text(c["item"].url, urls) if urls else None
                st, e, n = _number_check(text or "", brand, num_entries)
                c["num"], c["num_entry"], c["num_value"], c["num_from"] = st, e, n, "写真のタグ"

    keep = []
    for c in top:
        if c.get("num") == "match":
            c["entry"] = c["num_entry"]
            c["level"] = "strong"
            c["checks"].append({"text": f"型番一致 ✓ {c['num_value']}（{c['num_from']}）", "ok": True})
            stats["num_match"] += 1
            keep.append(c)
            continue
        if c.get("num") == "other" and c["entry"].get("numbers"):
            stats["num_excluded"] += 1
            print(f"    型番が違うので除外: {c['num_value']}（{c['num_from']}）{c['item'].title[:40]}")
            continue
        if c.get("num") == "other":
            c["checks"].append({"text": f"型番 {c['num_value']}（登録に型番なし・要確認）", "ok": False})
        keep.append(c)

    # 2) ファスナーの色（型番で一致したものは見ない）
    zip_todo = [c for c in keep if c["entry"].get("zipper") and c.get("num") != "match"]
    if zip_todo:
        ok, reason = ai_ready()
        if not ok:
            stats["zip_skipped"] += len(zip_todo)
            print(f"    ファスナーの色の確認を省きました（{reason}）。写真が似ている出品はそのまま残します")
            for c in zip_todo:
                c["checks"].append({"text": "ファスナーは未確認（Mac内AIが使えません）", "ok": False})
        else:
            need = [c["item"] for c in zip_todo if c["item"].url not in image_urls]
            if need:
                image_urls.update(scan_main._get_image_urls(need, page))
            out = []
            for i, c in enumerate(zip_todo):
                if should_stop and should_stop():
                    break
                if progress_note:
                    progress_note(f"ファスナーの色を確認中（{i + 1}/{len(zip_todo)}・Mac内AI）")
                want = c["entry"]["zipper"]
                got = _zipper_of_item(c["item"], image_urls.get(c["item"].url) or [])
                label = "金属色" if want == "金属" else want
                if got is None:
                    stats["zip_skipped"] += 1
                    c["checks"].append({"text": "ファスナーは未確認（Mac内AIの応答なし）", "ok": False})
                elif got == "不明":
                    stats["zip_unknown"] += 1
                    c["checks"].append({"text": "ファスナーの色は写真から不明（要確認）", "ok": False})
                elif got != want:
                    stats["zip_excluded"] += 1
                    print(f"    ファスナーが{'金属色' if got == '金属' else got}なので除外: {c['item'].title[:40]}")
                    c["drop"] = True
                else:
                    stats["zip_ok"] += 1
                    c["checks"].append({"text": f"ファスナー {label} ✓", "ok": True})
                out.append(c)
            keep = [c for c in keep if not c.get("drop")]
    return keep + rest


# ──────────────────────────────────────────────────────────────────────────────
# リサーチ本体
# ──────────────────────────────────────────────────────────────────────────────

def _groups(entries: list[dict]) -> list[dict]:
    """同じ ブランド × 検索する言葉 のモデルは1回の検索でまとめて照合する"""
    import my_list
    out: dict[tuple[str, str], dict] = {}
    for e in entries:
        src = {"name": e["brand"], "name_en": "", "aliases": []}
        brand = my_list.canonical_brand(src) or e["brand"]
        key = (brand, my_list.normalize_name(e.get("query") or ""))
        g = out.setdefault(key, {"brand": brand, "query": e.get("query") or "", "entries": [],
                                 "alias": {"_brand": brand, "name": e["brand"],
                                           "name_en": my_list.auto_name_en(src), "aliases": []}})
        g["entries"].append(e)
    return list(out.values())


def plan_size(ids: list[str] | None = None) -> int:
    return len(_groups(select_entries(ids)))


def scan(req: dict, settings: dict, sources=None, progress=None, should_stop=None, live=None) -> list[Deal]:
    """登録したモデルを探す（Playwright のブラウザを1つ起動して使い回す。free_research.scan と同じ作り）"""
    from playwright.sync_api import sync_playwright

    import analyzer
    import free_research
    import local_inspector
    import main as scan_main
    import my_list
    import ocr_reader
    import photo_reader
    from scrapers import mercari as mercari_scraper
    from scrapers import yahoo_auctions, vector_park, trefac_fashion, rakuma, yahoo_flea_market, yahoo_shopping

    if not available():
        raise ValueError("この Mac では写真の照合が使えません（macOS と Vision の部品が必要です）")
    entries = select_entries(req.get("model_ids") or None)
    if not entries:
        raise ValueError("調べるモデルがありません（モデルを登録して「探す」にしてください）")
    groups = _groups(entries)
    started = time.time()
    for k in stats:
        stats[k] = 0
    analyzer.reset_session()
    photo_reader.reset_session(settings)
    ocr_reader.reset_session(settings)
    local_inspector.reset_session(settings)
    analyzer.set_photo_check(photo_reader.condition_enabled() or local_inspector.enabled())
    yahoo_auctions.reset_retry_state()
    for mod in (yahoo_auctions, mercari_scraper, vector_park, trefac_fashion, rakuma, yahoo_flea_market, yahoo_shopping):
        mod._image_urls.clear()
    yahoo_shopping._descriptions.clear()
    database.init_db()
    _cache_init()
    _attr_init()
    my_list.register_aliases([g["alias"] for g in groups])
    for g in groups:
        free_research.ensure_aliases(g["brand"])

    print("=" * 50)
    print(f"登録モデルでリサーチ開始: モデル {len(entries)}件（{'・'.join(e['name'] for e in entries[:6])}"
          f"{' ほか' if len(entries) > 6 else ''}）/ 検索 {len(groups)}組 / 仕入れ先 {','.join(sorted(sources or []))}")
    print("=" * 50)

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = mercari_scraper.new_page(browser)
            deals: list[Deal] = []
            for i, g in enumerate(groups):
                if should_stop and should_stop():
                    print("\n[中断] ユーザー操作によりリサーチを中断しました")
                    break
                if progress:
                    progress(i, len(groups), f"{g['brand']} {g['query']}".strip())
                try:
                    deals += _scan_group(page, g, settings, sources, i, len(groups), progress, should_stop,
                                         scan_main, live)
                except Exception as e:
                    print(f"  [エラー] {g['brand']} {g['query']} を飛ばしました: {e}")
            deals = scan_main._check_photo_conditions(deals, settings, page, len(groups),
                                                      progress=progress, should_stop=should_stop, live=live)
            uniq: dict[str, Deal] = {}
            for d in deals:
                if d.item.url not in uniq or d.estimated_profit > uniq[d.item.url].estimated_profit:
                    uniq[d.item.url] = d
            return free_research._merge_cross_listed(list(uniq.values()))
        finally:
            browser.close()
    finally:
        playwright.stop()
        stats["seconds"] = time.time() - started
        print(f"\n[登録モデル統計] 所要 {stats['seconds'] / 60:.1f}分 / 取得 {stats['searched']}件 → タイトルの条件に合う {stats['titled']}件 → "
              f"写真を照合 {stats['compared']}件（新しく取得 {stats['downloaded']}枚・保存済み {stats['cached']}枚・写真なし {stats['noimg']}件）→ "
              f"一致 {stats['strong']}件・似ている（要確認） {stats['weak']}件 / 型番一致 {stats['num_match']}件・型番違いで除外 {stats['num_excluded']}件 / "
              f"ファスナー ✓{stats['zip_ok']}件・違いで除外 {stats['zip_excluded']}件・不明 {stats['zip_unknown']}件・未確認 {stats['zip_skipped']}件")


def _deal_for(it, e: dict, brand: str, commission, ship, min_profit):
    import analyzer
    market = MarketPrice(keyword=f"{brand} {e['name']}", avg_price=e["price"], median_price=e["price"],
                         min_price=e["price"], max_price=e["price"], sample_count=0)
    return analyzer.calculate_deal(it, market.keyword, market, commission_rate=commission,
                                   shipping_cost=ship, min_profit=min_profit)


def _scan_group(page, g: dict, settings: dict, sources, index: int, total: int, progress, should_stop,
                scan_main, live=None) -> list[Deal]:
    import analyzer
    import free_research
    import model_extractor

    brand, query, entries = g["brand"], g["query"], g["entries"]
    strong = float(settings.get("model_match_strong", DEFAULTS["model_match_strong"]))
    weak = min(strong, float(settings.get("model_match_weak", DEFAULTS["model_match_weak"])))
    commission = settings["mercari_commission_rate"]
    ship = settings["assumed_shipping_cost"]
    min_profit = settings["min_profit_yen"]
    per_source = settings["search_items_per_source"]

    # 登録写真の特徴量
    refs = []   # (モデル, 写真ファイル, ベクトル)
    for e in entries:
        for file, v in _entry_vectors(e):
            refs.append((e, file, v))
    label = f"{brand} {query}".strip()
    print(f"\n■ [{label}] 登録モデル {len(entries)}件（{'・'.join(e['name'] + ' ¥' + format(e['price'], ',') for e in entries)}）")
    if not refs:
        print("  登録写真を読み込めませんでした（写真を登録し直してください）")
        return []

    # 1) 仕入れ先を検索。仕入れ値の上限 = いちばん高い相場で、新品同様（係数1.30）でも利益が出なくなる値段
    top = max(e["price"] for e in entries)
    max_buy = int(top * 1.30 * (1 - commission)) - ship - min_profit
    if max_buy <= 0:
        print("  相場が低く、最低利益額に届く仕入れ値がありません")
        return []
    queries = [f"{bq} {query}".strip() for bq in free_research.brand_queries(brand)]
    fetchers = scan_main.source_fetchers(page, settings, max_buy, 0, per_source, [], False)
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
                progress(index, total, f"{q}（{scan_main.SOURCE_LABELS[key]}）", no - 1, n_search)
            got = fetch(q)
            new = [it for it in got if it.url and it.url not in seen]
            seen.update(it.url for it in new)
            print(f"  [{scan_main.SOURCE_LABELS[key]}] 「{q}」 {len(got)}件（新規 {len(new)}件）")
            items += new
            time.sleep(1)
    stats["searched"] += len(items)

    # 2) タイトル（ブランド名あり・「〜風」でない・偽物や箱だけでない）
    like_re = free_research._brand_like_re(brand)
    kept = []
    for it in items:
        t = free_research._norm(it.title)
        if not model_extractor.has_brand(it.title, brand) or like_re.search(t) or _BAD_RE.search(t):
            continue
        kept.append(it)
    print(f"  タイトルの条件に合う出品 {len(kept)}件 / 取得 {len(items)}件")
    stats["titled"] += len(kept)
    if not kept:
        return []

    # 3) Yahoo!ショッピングの送料を足して、仕入れ値の上限を確認し直す
    kept = scan_main.fill_shopping_postage(kept, settings, should_stop)
    kept = [it for it in kept if it.price <= max_buy]

    # 4) 写真の照合（出品の1枚目の写真 × 登録写真。いちばん似ている登録モデルを採用）
    def on_step(done, n):
        if progress:
            progress(index, total, f"{label}：写真を照合中（{done}/{n}）", n_search, n_search)
    vectors = _item_vectors(kept, should_stop, on_step)
    stats["compared"] += len(vectors)
    cands = []
    for it in kept:
        v = vectors.get(it.url)
        if v is None:
            continue
        best_sim, best_e, best_file = -1.0, None, ""
        for e, file, rv in refs:
            s = similarity(v, rv)
            if s > best_sim:
                best_sim, best_e, best_file = s, e, file
        if best_e is None or best_sim < weak:
            continue
        # 利益が出ない出品は確かめる前に外す（相場は登録モデルごと）
        if _deal_for(it, best_e, brand, commission, ship, min_profit) is None:
            continue
        cands.append({"item": it, "entry": best_e, "sim": best_sim, "file": best_file,
                      "level": "strong" if best_sim >= strong else "weak", "checks": []})

    # 5) 型番・ファスナーの色で確かめる（登録に指定があるときだけ。似ている度の高い候補から _CHECK_TOP 件）
    n_before = len(cands)
    if cands:
        def note(text):
            if progress:
                progress(index, total, f"{label}：{text}", n_search, n_search)
        cands = _verify(cands, g, page, scan_main, note, should_stop)
        if len(cands) != n_before:
            print(f"  型番・ファスナーの確認: 写真が似ている {n_before}件 → {len(cands)}件")

    deals: list[Deal] = []
    n_strong = n_weak = 0
    for c in cands:
        it, best_e, best_sim, best_file, level = c["item"], c["entry"], c["sim"], c["file"], c["level"]
        if best_file and not best_file.startswith(best_e["id"] + "_"):
            best_file = (best_e.get("photos") or [{"file": ""}])[0]["file"]   # 型番で別の登録モデルに決まったとき
        deal = _deal_for(it, best_e, brand, commission, ship, min_profit)
        if deal is None:
            continue
        if level == "strong":
            n_strong += 1
        else:
            n_weak += 1
        deal.brand = brand
        deal.model = ""
        deal.category = best_e["name"]
        deal.search_keyword = label
        deal.free_info = {
            "mode": "model", "basis": "registered",
            "confidence": "高" if level == "strong" else "中",
            "gender": "", "genre_market": None, "model_market": None, "features": [],
            "registered": {"id": best_e["id"], "name": best_e["name"], "price": best_e["price"],
                           "similarity": round(best_sim, 3), "level": level, "photo": best_file,
                           "checks": c.get("checks") or []},
        }
        deals.append(deal)
        if live:
            live.deal(deal)
    stats["strong"] += n_strong
    stats["weak"] += n_weak
    deals.sort(key=lambda d: d.estimated_profit, reverse=True)
    print(f"  写真が一致 {n_strong}件・似ている（要確認） {n_weak}件（利益の条件に合うもの）")
    print(f"  --> {len(deals)} 件の案件 [登録モデル]")
    return deals
