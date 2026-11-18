"""
学校のブランド一覧（course_brands.json）をマイブランド登録（my_list.json）に取り込む

使い方:
  ./venv/bin/python import_brands.py <course_brands.json のパス>

・取り込んだブランドはオフ（ルイヴィトンだけオン）。画面の「マイブランド登録」で調べたいものをオンにする
・同じブランド（表記ゆれ込み）はもう一度追加しない（英字表記・ジャンルが空なら補うだけ）。何度実行しても大丈夫
・ジャンルが空のブランドは、リサーチのときにグループの既定のジャンル（画面で変えられる）を使う
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import my_list  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"ファイルがありません: {path}")
        return 1
    r = my_list.import_course(path)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    print(f"保存先: {my_list.LIST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
