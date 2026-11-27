"""登録モデル（model_match）をネット上で共有する。

置き場所は GitHub の（非公開）リポジトリ。中身は
  models/<id>.json   … ブランド・モデル名・検索する言葉・相場・写真のファイル名
  photos/<ファイル名> … 登録写真（JPEG）
読み書きには「合言葉」（そのリポジトリだけに使える GitHub のアクセストークン）が要る。
合言葉が空なら、この Mac の git に保存されている GitHub のログインを使う（持ち主の Mac 用）。

共有しないもの: オン/オフ（人ごとの好み）と写真の特徴量（Mac ごとに計算する）。
設定と状態は model_share.json（git 管理外・600。合言葉を含むので人に渡さない）。
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime

import requests

import model_match

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(BASE_DIR, "model_share.json")
API = "https://api.github.com"
_TIMEOUT = 30
_PHOTO_MAX = 5 * 1024 * 1024
_ID_RE = re.compile(r"[0-9a-f]{10}")
_PHOTO_RE = re.compile(r"[0-9a-f]{10}_[0-9a-f]{8}\.jpg")
_REPO_RE = re.compile(r"^(?:https?://github\.com/)?([A-Za-z0-9][A-Za-z0-9\-]{0,38})/([A-Za-z0-9._\-]{1,100}?)(?:\.git)?/?$")
_lock = threading.RLock()


class ShareError(Exception):
    """画面にそのまま出せる文のエラー"""


# ──────────────────────────────────────────────────────────────────────────────
# 設定・状態
# ──────────────────────────────────────────────────────────────────────────────

def _conf() -> dict:
    try:
        with open(CONF_PATH, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("repo", "")
    c.setdefault("token", "")
    c.setdefault("known", [])      # 前回の同期で共有にあったモデルのID（消されたかどうかの判定用）
    c.setdefault("dirty", [])      # 送れなかった変更があるモデルのID（次の同期で送る）
    c.setdefault("trash", [])      # 消したのに共有から消せなかったモデルのID
    c.setdefault("last_sync", "")
    c.setdefault("last_error", "")
    return c


def _save_conf(c: dict) -> None:
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF_PATH)


def connected() -> bool:
    return bool(_conf()["repo"])


def status() -> dict:
    c = _conf()
    return {"connected": bool(c["repo"]), "repo": c["repo"],
            "url": f"https://github.com/{c['repo']}" if c["repo"] else "",
            "has_key": bool(c["token"]), "last_sync": c["last_sync"], "last_error": c["last_error"],
            "pending": len(set(c["dirty"]) | set(c["trash"]))}


def _git_token() -> str:
    try:
        out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                             capture_output=True, text=True, timeout=10,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}).stdout
    except Exception:
        return ""
    m = re.search(r"^password=(.+)$", out, re.M)
    return m.group(1).strip() if m else ""


# ──────────────────────────────────────────────────────────────────────────────
# GitHub
# ──────────────────────────────────────────────────────────────────────────────

class _Hub:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token or _git_token()
        if not self.token:
            raise ShareError("合言葉を入れてください")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "model-share"})
        self.tree: dict[str, str] = {}

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        try:
            return self.s.request(method, f"{API}/repos/{self.repo}{path}", timeout=_TIMEOUT, **kw)
        except requests.RequestException:
            raise ShareError("ネットにつながりません（共有の置き場所に届きませんでした）")

    def _fail(self, r: requests.Response, what: str):
        if r.status_code == 401:
            raise ShareError("合言葉が違うか、期限が切れています")
        if r.status_code == 404:
            raise ShareError("共有の置き場所が見つかりません（URL か合言葉が違います）")
        if r.status_code == 403:
            raise ShareError("この合言葉では書き込めません（読み書きできる合言葉をもらってください）"
                             if "rate limit" not in r.text.lower() else "GitHub の回数制限にかかりました。しばらく待ってください")
        raise ShareError(f"{what}に失敗しました（GitHub {r.status_code}）")

    def check(self) -> None:
        r = self._req("GET", "")
        if r.status_code != 200:
            self._fail(r, "接続")
        perm = r.json().get("permissions") or {}
        if perm and not perm.get("push"):
            raise ShareError("この合言葉では書き込めません（読み書きできる合言葉をもらってください）")

    def load_tree(self) -> dict[str, str]:
        r = self._req("GET", "/git/trees/HEAD", params={"recursive": "1"})
        if r.status_code in (404, 409):   # 404/409 = まだ何も入っていない置き場所
            chk = self._req("GET", "")
            if chk.status_code != 200:
                self._fail(chk, "読み込み")
            self.tree = {}
            return self.tree
        if r.status_code != 200:
            self._fail(r, "読み込み")
        self.tree = {t["path"]: t["sha"] for t in r.json().get("tree", []) if t.get("type") == "blob"}
        return self.tree

    def blob(self, sha: str) -> bytes:
        r = self._req("GET", f"/git/blobs/{sha}")
        if r.status_code != 200:
            self._fail(r, "読み込み")
        return base64.b64decode(r.json().get("content", ""))

    def _sha(self, path: str) -> str | None:
        r = self._req("GET", f"/contents/{path}")
        return r.json().get("sha") if r.status_code == 200 else None

    def put(self, path: str, data: bytes, msg: str) -> None:
        if self.tree.get(path) == _blob_sha(data):
            return
        for attempt in range(3):
            body = {"message": msg, "content": base64.b64encode(data).decode("ascii")}
            if self.tree.get(path):
                body["sha"] = self.tree[path]
            r = self._req("PUT", f"/contents/{path}", json=body)
            if r.status_code in (200, 201):
                self.tree[path] = r.json()["content"]["sha"]
                return
            if r.status_code in (409, 422) and attempt < 2:   # ほかの人の書き込みと重なった → 最新を見てやり直す
                time.sleep(0.6 * (attempt + 1))
                sha = self._sha(path)
                if sha:
                    self.tree[path] = sha
                else:
                    self.tree.pop(path, None)
                continue
            self._fail(r, "書き込み")

    def delete(self, path: str, msg: str) -> None:
        for attempt in range(3):
            sha = self.tree.get(path) or self._sha(path)
            if not sha:
                self.tree.pop(path, None)
                return
            r = self._req("DELETE", f"/contents/{path}", json={"message": msg, "sha": sha})
            if r.status_code in (200, 404):
                self.tree.pop(path, None)
                return
            if r.status_code in (409, 422) and attempt < 2:
                time.sleep(0.6 * (attempt + 1))
                self.tree.pop(path, None)
                continue
            self._fail(r, "削除")


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _hub(c: dict | None = None) -> _Hub:
    c = c or _conf()
    if not c["repo"]:
        raise ShareError("共有につないでいません")
    return _Hub(c["repo"], c["token"])


# ──────────────────────────────────────────────────────────────────────────────
# 送る・受け取る
# ──────────────────────────────────────────────────────────────────────────────

def _remote_doc(e: dict) -> bytes:
    doc = {"id": e["id"], "brand": e["brand"], "name": e["name"], "query": e.get("query", ""),
           "price": e["price"], "photos": [p["file"] for p in e.get("photos", [])],
           "created_at": e.get("created_at", ""), "updated_at": e.get("updated_at", "")}
    # 型番・ファスナーの色（2026-11-27 追加）は入っているときだけ載せる（入っていない登録は前と同じ内容のまま＝送り直さない）
    if e.get("numbers"):
        doc["numbers"] = list(e["numbers"])
    if e.get("zipper"):
        doc["zipper"] = e["zipper"]
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _label(e: dict) -> str:
    return f"{e.get('brand', '')} {e.get('name', '')}".strip()[:80]


def _push_entry(hub: _Hub, e: dict, verb: str = "更新") -> None:
    """写真を先に送り、最後にモデルの情報を送る（読む側が写真の無いモデルを見ないように）"""
    for p in e.get("photos", []):
        path = f"photos/{p['file']}"
        if path in hub.tree:
            continue
        local = model_match.photo_path(p["file"])
        if not local:
            continue
        with open(local, "rb") as f:
            hub.put(path, f.read(), f"写真: {_label(e)}")
    hub.put(f"models/{e['id']}.json", _remote_doc(e), f"{verb}: {_label(e)}")


def _drop_remote(hub: _Hub, entry_id: str, files: list[str], label: str = "") -> None:
    hub.delete(f"models/{entry_id}.json", f"削除: {label or entry_id}")
    for f in files:
        hub.delete(f"photos/{f}", f"写真を削除: {label or entry_id}")


def _or_empty(fn, v, empty):
    """任意の項目（型番・ファスナーの色）は形がおかしければ空にする（モデルごと捨てない）"""
    try:
        return fn(v)
    except Exception:
        return empty


def _clean_remote(doc: dict, entry_id: str) -> dict | None:
    """共有から来た内容を確かめる（ほかの人が書いたものなので、形がおかしければ使わない）"""
    try:
        if doc.get("id") != entry_id:
            return None
        photos = [f for f in doc.get("photos", []) if isinstance(f, str) and _PHOTO_RE.fullmatch(f)
                  and f.startswith(entry_id + "_")][:model_match.MAX_PHOTOS]
        if not photos:
            return None
        return {"id": entry_id,
                "brand": model_match._text(doc.get("brand"), "ブランド", True),
                "name": model_match._text(doc.get("name"), "モデル名", True),
                "query": model_match._text(doc.get("query"), "検索する言葉", False),
                "price": model_match._price(doc.get("price")),
                "numbers": _or_empty(model_match._numbers, doc.get("numbers") or [], []),
                "zipper": _or_empty(model_match._zipper, doc.get("zipper"), ""),
                "photos": photos,
                "created_at": str(doc.get("created_at") or "")[:19],
                "updated_at": str(doc.get("updated_at") or "")[:19]}
    except Exception:
        return None


def _fetch_photo(hub: _Hub, file: str) -> dict | None:
    """共有の写真をこの Mac に保存し、特徴量を計算する。すでにあれば何もしない"""
    path = os.path.join(model_match.PHOTO_DIR, file)
    if not os.path.isfile(path):
        sha = hub.tree.get(f"photos/{file}")
        if not sha:
            return None
        raw = hub.blob(sha)
        if len(raw) > _PHOTO_MAX or model_match._to_jpeg(raw, 64) is None:
            return None
        os.makedirs(model_match.PHOTO_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
    out = {"file": file}
    try:
        with open(path, "rb") as f:
            fp = model_match.fingerprint(f.read())
        if fp is not None:
            out["fp"] = base64.b64encode(model_match._pack(fp)).decode("ascii")
            out["fp_version"] = model_match.FP_VERSION
    except Exception:
        pass
    return out


def _apply_remote(hub: _Hub, local: dict | None, r: dict) -> dict | None:
    """共有の内容をこの Mac の登録に反映した結果（写真が1枚も取れなければ None）"""
    have = {p["file"]: p for p in (local or {}).get("photos", [])}
    photos = []
    for f in r["photos"]:
        p = have.get(f) if model_match.photo_path(f) else None
        p = p or _fetch_photo(hub, f)
        if p:
            photos.append(p)
    if not photos:
        return None
    for f in have:
        if f not in r["photos"]:
            model_match._unlink(f)
    return {"id": r["id"], "brand": r["brand"], "name": r["name"], "query": r["query"], "price": r["price"],
            "numbers": r.get("numbers") or [], "zipper": r.get("zipper", ""),
            "enabled": bool((local or {}).get("enabled", True)), "photos": photos,
            "created_at": r["created_at"] or (local or {}).get("created_at", ""),
            "updated_at": r["updated_at"]}


def sync() -> dict:
    """共有とこの Mac の登録を合わせる。返り値: {added, updated, removed, sent}"""
    with _lock:
        c = _conf()
        hub = _hub(c)
        stat = {"added": 0, "updated": 0, "removed": 0, "sent": 0}
        try:
            hub.load_tree()
            known, dirty, trash = set(c["known"]), set(c["dirty"]), set(c["trash"])

            # 1) こちらで消したのに共有に残っているもの
            for eid in sorted(trash):
                files = [p[7:] for p in hub.tree if p.startswith(f"photos/{eid}_")]
                _drop_remote(hub, eid, files)
                trash.discard(eid)
                known.discard(eid)

            remote_ids = {m.group(1) for p in hub.tree for m in [re.fullmatch(r"models/([0-9a-f]{10})\.json", p)] if m}

            with model_match._lock:
                data = model_match.load()
                by_id = {e["id"]: e for e in data["entries"]}
                changed = False

                # 2) 共有 → この Mac
                for eid in sorted(remote_ids):
                    loc = by_id.get(eid)
                    if eid in dirty and loc:
                        continue                      # こちらの変更をまだ送れていない → 下で送る
                    sha = hub.tree[f"models/{eid}.json"]
                    if loc and _blob_sha(_remote_doc(loc)) == sha \
                            and all(model_match.photo_path(p["file"]) for p in loc["photos"]):
                        continue                      # 同じ内容
                    try:
                        r = _clean_remote(json.loads(hub.blob(sha).decode("utf-8")), eid)
                    except ValueError:
                        r = None
                    new = _apply_remote(hub, loc, r) if r else None
                    if not new:
                        continue
                    if loc:
                        loc.clear()
                        loc.update(new)
                        stat["updated"] += 1
                    else:
                        data["entries"].append(new)
                        by_id[eid] = new
                        stat["added"] += 1
                    changed = True

                # 3) 共有から消されたもの（前回はあったのに今は無い）
                for eid in [i for i in by_id if i not in remote_ids and i in known and i not in dirty]:
                    model_match._remove_files(by_id.pop(eid))
                    data["entries"] = [e for e in data["entries"] if e["id"] != eid]
                    stat["removed"] += 1
                    changed = True

                if changed:
                    model_match.save(data)
                to_send = [e for e in data["entries"] if e["id"] not in remote_ids or e["id"] in dirty]

            # 4) この Mac → 共有（まだ共有に無いもの・送れていなかった変更）
            for e in to_send:
                _push_entry(hub, e, "登録" if e["id"] not in remote_ids else "更新")
                remote_ids.add(e["id"])
                dirty.discard(e["id"])
                stat["sent"] += 1

            c.update(known=sorted(remote_ids), dirty=sorted(dirty), trash=sorted(trash),
                     last_sync=datetime.now().isoformat(timespec="seconds"), last_error="")
            _save_conf(c)
            return stat
        except ShareError as ex:
            c2 = _conf()
            c2["last_error"] = str(ex)
            _save_conf(c2)
            raise


def sync_if_stale(seconds: int = 30) -> str:
    """つないでいて、前回の同期から時間がたっていれば同期する。失敗したら画面に出す文を返す"""
    c = _conf()
    if not c["repo"]:
        return ""
    try:
        if c["last_sync"] and not c["last_error"]:
            if (datetime.now() - datetime.fromisoformat(c["last_sync"])).total_seconds() < seconds:
                return ""
    except ValueError:
        pass
    try:
        sync()
        return ""
    except ShareError as ex:
        return str(ex)
    except Exception as ex:
        return f"共有の同期に失敗しました（{type(ex).__name__}）"


def _mark(key: str, entry_id: str, on: bool) -> None:
    c = _conf()
    s = set(c[key])
    (s.add if on else s.discard)(entry_id)
    c[key] = sorted(s)
    _save_conf(c)


def push(entry_id: str, verb: str = "更新", removed_photo: str = "") -> str:
    """登録・編集のあとに呼ぶ。失敗したら次の同期で送る印を付け、画面に出す文を返す"""
    with _lock:
        c = _conf()
        if not c["repo"]:
            return ""
        _mark("dirty", entry_id, True)
        try:
            e = next((x for x in model_match.load()["entries"] if x["id"] == entry_id), None)
            if e is None:
                return ""
            hub = _hub(c)
            hub.load_tree()
            _push_entry(hub, e, verb)
            if removed_photo and _PHOTO_RE.fullmatch(removed_photo):
                hub.delete(f"photos/{removed_photo}", f"写真を削除: {_label(e)}")
            c = _conf()
            c["dirty"] = sorted(set(c["dirty"]) - {entry_id})
            c["known"] = sorted(set(c["known"]) | {entry_id})
            c["last_error"] = ""
            _save_conf(c)
            return ""
        except ShareError as ex:
            return f"この Mac には保存しました。共有にはまだ送れていません（{ex}）。次の同期で送ります"
        except Exception as ex:
            return f"この Mac には保存しました。共有にはまだ送れていません（{type(ex).__name__}）。次の同期で送ります"


def remove(entry_id: str, files: list[str], label: str = "") -> str:
    """モデルを消したあとに呼ぶ"""
    with _lock:
        c = _conf()
        if not c["repo"]:
            return ""
        _mark("trash", entry_id, True)
        _mark("dirty", entry_id, False)
        try:
            hub = _hub(c)
            hub.load_tree()
            _drop_remote(hub, entry_id, [f for f in files if _PHOTO_RE.fullmatch(f)], label)
            c = _conf()
            c["trash"] = sorted(set(c["trash"]) - {entry_id})
            c["known"] = sorted(set(c["known"]) - {entry_id})
            _save_conf(c)
            return ""
        except ShareError as ex:
            return f"この Mac からは消しました。共有からはまだ消せていません（{ex}）。次の同期で消します"
        except Exception as ex:
            return f"この Mac からは消しました。共有からはまだ消せていません（{type(ex).__name__}）。次の同期で消します"


def connect(url: str, token: str) -> dict:
    """共有につなぐ。つないだらすぐ同期する（この Mac の登録は共有に足される）"""
    m = _REPO_RE.match(str(url or "").strip())
    if not m:
        raise ShareError("共有のURLは https://github.com/名前/置き場所 の形で入れてください")
    repo = f"{m.group(1)}/{m.group(2)}"
    token = str(token or "").strip()
    if token and not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
        raise ShareError("合言葉の形が違います（もらった文字をそのまま貼ってください）")
    with _lock:
        _Hub(repo, token).check()
        c = _conf()
        if c["repo"] != repo:
            c.update(known=[], dirty=[], trash=[], last_sync="")
        c.update(repo=repo, token=token, last_error="")
        _save_conf(c)
        return sync()


def disconnect() -> None:
    """共有をやめる（この Mac の登録はそのまま残る。共有の中身も消さない）"""
    with _lock:
        try:
            os.remove(CONF_PATH)
        except OSError:
            pass
