"""
【本人の Mac 用】ブランド一覧（course_brands.json。git 管理外）をパスワードで暗号化して brands.enc を作り直す

使い方:
  ./venv/bin/python export_brands.py [course_brands.json のパス]
  → brands.enc を書き出したら、git add brands.enc → commit → push すると、仲間の Mac では次の起動時に
    「まだ取り込んだことのないブランドだけ」自動で追加される（仲間が消したり変えたりした登録はそのまま）。

・パスワードは .brands_password（git 管理外・600）。無ければ覚えやすい12文字（小文字と数字。0/o/1/l/i など紛らわしい文字なし）を作って保存する。
  パスワードを変えたいときは .brands_password を消してから実行（仲間には新しいパスワードを伝え直す必要がある）。
・平文の course_brands.json は絶対にコミットしないこと（.gitignore 済み）。
・仲間に配るのはブランド名・英字表記・グループ・ジャンルだけ（自分のオン/オフや上限価格などは入らない）。
"""

from __future__ import annotations

import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import brands_sync  # noqa: E402

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # 0 o 1 l i を除いた小文字と数字


def new_password(n: int = 12) -> str:
    while True:
        pw = "".join(secrets.choice(ALPHABET) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def main() -> int:
    src_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(brands_sync.BASE_DIR, "course_brands.json")
    if not os.path.exists(src_path):
        print(f"ファイルがありません: {src_path}")
        return 1
    with open(src_path, encoding="utf-8") as f:
        src = json.load(f)
    # 配るのはブランドの情報だけ（形を整え直す。余計なキーは入れない）
    out = {"source": str(src.get("source") or ""), "groups": []}
    n = 0
    for g in src.get("groups") or []:
        brands = []
        for b in g.get("brands") or []:
            if isinstance(b, str):
                b = {"name_ja": b}
            brands.append({"name_ja": str(b.get("name_ja") or ""), "name_en": str(b.get("name_en") or ""),
                           "genres": list(b.get("genres") or []), "note": str(b.get("note") or "")})
        out["groups"].append({"group": str(g.get("group") or ""), "brands": brands})
        n += len(brands)

    pw = brands_sync.read_saved_password()
    created = False
    if not pw:
        pw = new_password()
        brands_sync.save_password(pw)
        created = True
    plain = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = brands_sync.encrypt(plain, pw)
    assert brands_sync.decrypt(blob, pw) == plain
    with open(brands_sync.ENC_PATH, "wb") as f:
        f.write(blob)
    print(f"brands.enc を書き出しました（{len(out['groups'])}グループ・{n}件）。")
    if created:
        print(f"新しいパスワードを作って .brands_password に保存しました: {pw}")
    else:
        print("パスワードは .brands_password のものを使いました（変わっていません）。")
    print("仲間に届けるには: git add brands.enc && git commit && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
