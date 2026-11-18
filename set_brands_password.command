#!/bin/bash
# ダブルクリックで「ブランド一覧のパスワード」を入れる（利益発見ツール2・仲間の Mac 用）
#
# ・紹介者から聞いたパスワードを入れると、ブランド一覧（brands.enc。暗号化してある）を開いて
#   マイブランド登録に取り込みます（まだ入っていないブランドだけ追加。自分で追加・編集・削除した登録はそのまま）。
# ・パスワードはこのフォルダの .brands_password に保存します（git の管理外・自分だけ読める設定）。
#   以後はツールを起動するたびに、ブランド一覧が更新されていれば自動で取り込みます。
# ・パスワードが違っても、入れなくても、ツールは使えます（ブランド一覧が空なだけ）。
# ・このファイル自体にはパスワードは入っていません。
#
# オプション: --startup  起動時（start.command）から呼ぶとき。「あとで」で閉じられ、1回だけ聞く
# テスト用:   環境変数 RIEKI2_BRANDS_PASSWORD があれば、ダイアログを出さずにその値を使う（1回だけ試す）

main() {
    cd "$(dirname "$0")" || exit 1
    local TITLE="利益発見ツール2（ブランド一覧のパスワード）"
    local mode="${1:-}"
    local tries=3
    [ "$mode" = "--startup" ] && tries=1

    if [ ! -x ./venv/bin/python ]; then
        echo "[注意] ツールの準備（セットアップ）がまだ終わっていません。先にセットアップを完了させてください。"
        return 0
    fi

    local i=1 pw rc msg
    msg="紹介者から聞いた「ブランド一覧のパスワード」を入れて「OK」を押してください。

・英小文字と数字の12文字です（大文字・全角で打っても大丈夫）
・入れた文字は ●●● で隠れて表示されます
・分からないときは「あとで」を押してください（ツールはそのまま使えます）"
    while [ "$i" -le "$tries" ]; do
        if [ -n "${RIEKI2_BRANDS_PASSWORD+x}" ]; then
            pw="$RIEKI2_BRANDS_PASSWORD"
            tries=1
        else
            pw="$(osascript \
                -e 'on run argv' \
                -e 'set r to display dialog (item 1 of argv) default answer "" with hidden answer buttons {"あとで", "OK"} default button "OK" cancel button "あとで" with title (item 2 of argv) with icon note' \
                -e 'return text returned of r' \
                -e 'end run' "$msg" "$TITLE" 2>/dev/null)"
            if [ $? -ne 0 ]; then
                echo "ブランド一覧のパスワードの入力は「あとで」にしました。"
                echo "  （あとから入れるとき: ~/rieki-hakken-2 フォルダの「set_brands_password.command」をダブルクリック。"
                echo "    次にツールを起動したときにも入力画面が出ます）"
                return 0
            fi
        fi
        printf '%s\n' "$pw" | ./venv/bin/python brands_sync.py --set-password
        rc=$?
        pw=""
        case "$rc" in
            0)
                [ -n "${RIEKI2_BRANDS_PASSWORD+x}" ] || alert "ブランド一覧を取り込みました。" "画面の「マイブランド登録」で、調べたいブランドを「リサーチする」にしてください。"
                return 0 ;;
            2) echo "[お知らせ] パスワードが違います。"
               msg="パスワードが違うようです。もう一度入れてください（あと $((tries - i)) 回）。

・英小文字と数字の12文字です
・分からないときは「あとで」を押して、紹介者に確認してください" ;;
            3) echo "[お知らせ] パスワードが入力されていません。"
               msg="パスワードが入っていません。紹介者から聞いた12文字を入れてください。" ;;
            *) echo "[注意] ブランド一覧を取り込めませんでした（ツールはそのまま使えます）。"
               return 0 ;;
        esac
        i=$((i + 1))
    done
    echo "ブランド一覧はまだ取り込んでいません（ツールはそのまま使えます。マイブランド登録のブランド一覧が空なだけです）。"
    echo "  あとから入れるとき: ~/rieki-hakken-2 フォルダの「set_brands_password.command」をダブルクリック"
    echo "  （次にツールを起動したときにも入力画面が出ます）"
    return 0
}

# ダイアログでお知らせを出す（osascript が使えないときはターミナルに表示）
alert() {
    local msg="$1" detail="$2"
    osascript - "$msg" "$detail" >/dev/null 2>&1 <<'EOF' || { echo "$msg"; echo "$detail"; }
on run argv
    display alert (item 1 of argv) message (item 2 of argv) buttons {"OK"} default button "OK"
end run
EOF
}

main "$@"
exit $?
