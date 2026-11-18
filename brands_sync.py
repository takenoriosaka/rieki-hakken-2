"""
ブランド一覧（物販ONEのブランド一覧）の暗号化ファイル brands.enc を復号して、マイブランド登録（my_list.json）に取り込む

・brands.enc はパスワードで暗号化してあり（scrypt で鍵を作って AES-256-GCM）、パスワードを知らないと中身は読めない。
  作り直すのは本人の Mac の export_brands.py。
・パスワードはこのフォルダの .brands_password（git 管理外・600）に保存する。
・取り込みは「まだ取り込んだことのないブランドだけ追加」（my_list.import_course_new_only）。
  仲間が自分で追加・編集・削除・オン/オフした登録は上書きも復活もしない。
・前回取り込んだ brands.enc の SHA-256 を .brands_enc.sha256 に控え、変わったときだけ取り込む。
・どんなときもエラーで止めない（パスワードが違う・未入力でもツールはそのまま起動できる。ブランド一覧が空なだけ）。

使い方:
  ./venv/bin/python brands_sync.py --auto          # 起動時（start.command）。brands.enc が変わっていれば取り込む
  ./venv/bin/python brands_sync.py --set-password  # 標準入力の1行目をパスワードとして確かめ、合っていれば保存して取り込む
  ./venv/bin/python brands_sync.py --check         # 保存済みパスワードがあるか（終了コード 0=ある・合っている / 1=ない・違う）
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENC_PATH = os.path.join(BASE_DIR, "brands.enc")
PASSWORD_PATH = os.path.join(BASE_DIR, ".brands_password")
STAMP_PATH = os.path.join(BASE_DIR, ".brands_enc.sha256")

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
AAD = b"rieki-hakken-2 brands v1"


class WrongPassword(Exception):
    pass


def normalize_password(s: str) -> str:
    """全角で打った・前後に空白・大文字で打った、でも通るように（パスワードは小文字と数字だけで作る）"""
    t = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(t.split()).lower()


def _key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        normalize_password(password).encode("utf-8"))


def encrypt(plain: bytes, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt)).encrypt(nonce, plain, AAD)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    doc = {"format": "rieki-hakken-2 brands", "v": 1, "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R,
           "p": SCRYPT_P, "cipher": "AES-256-GCM", "salt": b64(salt), "nonce": b64(nonce), "ct": b64(ct)}
    return (json.dumps(doc, indent=1) + "\n").encode("ascii")


def decrypt(blob: bytes, password: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    doc = json.loads(blob.decode("ascii"))
    salt, nonce, ct = (base64.b64decode(doc[k]) for k in ("salt", "nonce", "ct"))
    key = Scrypt(salt=salt, length=32, n=int(doc["n"]), r=int(doc["r"]), p=int(doc["p"])).derive(
        normalize_password(password).encode("utf-8"))
    try:
        return AESGCM(key).decrypt(nonce, ct, AAD)
    except InvalidTag:
        raise WrongPassword() from None


def read_saved_password() -> str:
    try:
        with open(PASSWORD_PATH, encoding="utf-8") as f:
            return normalize_password(f.read())
    except OSError:
        return ""


def save_password(password: str) -> None:
    fd = os.open(PASSWORD_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(normalize_password(password) + "\n")
    os.chmod(PASSWORD_PATH, 0o600)


def _enc_hash() -> str:
    with open(ENC_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read_stamp() -> str:
    try:
        with open(STAMP_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def import_with(password: str) -> dict:
    """brands.enc を復号して取り込む（パスワードが違えば WrongPassword）"""
    with open(ENC_PATH, "rb") as f:
        blob = f.read()
    src = json.loads(decrypt(blob, password).decode("utf-8"))
    sys.path.insert(0, BASE_DIR)
    import my_list
    r = my_list.import_course_new_only(src)
    with open(STAMP_PATH, "w", encoding="utf-8") as f:
        f.write(hashlib.sha256(blob).hexdigest() + "\n")
    return r


HOW_TO_RETRY = ("（あとから入れ直すとき: Finder で「移動」→「ホーム」→「rieki-hakken-2」フォルダを開き、"
                "「set_brands_password.command」をダブルクリック。次にツールを起動したときにも入力画面が出ます）")


def _report(r: dict) -> None:
    if r["added"]:
        print(f"ブランド一覧から {r['added']} 件を追加しました（マイブランド登録 全{r['total']}件・オン{r['enabled']}件）。")
        print("  追加した分は「リサーチしない」になっています。画面の「マイブランド登録」で調べたいものをオンにしてください。")
    else:
        print(f"ブランド一覧に新しいブランドはありませんでした（マイブランド登録 全{r['total']}件）。")


def cmd_auto() -> int:
    if not os.path.exists(ENC_PATH):
        return 0
    pw = read_saved_password()
    if not pw:
        print("[お知らせ] ブランド一覧のパスワードがまだ入っていません。マイブランド登録のブランド一覧は空のままです。")
        print("  " + HOW_TO_RETRY)
        return 0
    if _enc_hash() == _read_stamp():
        return 0
    try:
        r = import_with(pw)
    except WrongPassword:
        print("[お知らせ] 保存されているブランド一覧のパスワードでは開けませんでした（パスワードが変わった可能性があります）。")
        print("  ツールはそのまま使えます。新しいパスワードを紹介者に確認してください。")
        print("  " + HOW_TO_RETRY)
        return 0
    print("ブランド一覧が更新されていたので取り込みました。")
    _report(r)
    return 0


def cmd_set_password() -> int:
    """終了コード: 0=合っていて取り込んだ / 2=パスワードが違う / 3=空 / 1=その他の失敗"""
    pw = normalize_password(sys.stdin.readline())
    if not pw:
        return 3
    if not os.path.exists(ENC_PATH):
        print("[注意] brands.enc が見つかりません。ツールを最新版に更新してからもう一度試してください。")
        return 1
    try:
        r = import_with(pw)
    except WrongPassword:
        return 2
    save_password(pw)
    print("パスワードを保存しました。")
    _report(r)
    return 0


def cmd_check() -> int:
    pw = read_saved_password()
    if not pw or not os.path.exists(ENC_PATH):
        return 1
    try:
        with open(ENC_PATH, "rb") as f:
            decrypt(f.read(), pw)
        return 0
    except WrongPassword:
        return 1


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if arg == "--auto":
            return cmd_auto()
        if arg == "--set-password":
            return cmd_set_password()
        if arg == "--check":
            return cmd_check()
    except ImportError:
        print("[注意] 暗号を開く部品（cryptography）が入っていないため、ブランド一覧を取り込めませんでした。")
        print("  次にツールを起動したときに自動で入れ直します。")
        return 1
    except Exception as e:  # 取り込みの失敗でツールの起動を止めない
        print(f"[注意] ブランド一覧の取り込みに失敗しました（{type(e).__name__}: {e}）。ツールはそのまま使えます。")
        return 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
