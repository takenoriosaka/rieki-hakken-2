#!/bin/bash
# =====================================================================
#  利益発見ツール2  初回セットアップ（仲間の Mac 用）
#
#  このファイル 1 つだけで、次のことを全部自動で行います。
#    1. git（Apple 純正の開発ツール）があるか確認
#    2. ツール本体をダウンロード（~/rieki-hakken-2。公開リポジトリなのでログイン不要）
#    3. Python とライブラリを準備（uv を使用。管理者パスワード不要）
#    4. ブラウザ部品（Chromium）を準備
#    5. ブランド一覧をマイブランド登録に取り込む（パスワード不要）
#    6. デスクトップに「利益発見ツール2」を作成（青いアイコン）
#    7. ツールを起動してブラウザで画面を開く（http://127.0.0.1:8766）
#
#  元の「利益発見ツール」（~/rieki-hakken・8765番・デスクトップの「利益発見ツール」）とは
#  保存先・番号・起動ファイルがすべて別なので、同じ Mac に両方入れても大丈夫です。
#
#  GitHub のアカウントやログインは不要です。
#  何度実行しても大丈夫です（済んでいる作業は飛ばします）。
#
#  開き方（どちらか）:
#    - ダブルクリック（開けないときは「右クリック → 開く」）
#    - ターミナルに次を貼り付けて Enter:
#        bash <(curl -fsSL https://raw.githubusercontent.com/takenoriosaka/rieki-hakken-2/main/setup_for_friend.command)
# =====================================================================

# --- 設定（テスト時は環境変数で差し替え可能） ---
#   RIEKI2_REPO_URL / RIEKI2_DIR / RIEKI2_DESKTOP / RIEKI2_UV_DIR / RIEKI2_BRANCH
#   RIEKI2_BRANDS_PASSWORD（暗号化方式に戻したときだけ: ブランド一覧のパスワードをダイアログなしで渡す）
#   RIEKI_SKIP_PLAYWRIGHT=1 / RIEKI_NO_LAUNCH=1 / RIEKI_NO_PAUSE=1
REPO_URL="${RIEKI2_REPO_URL:-https://github.com/takenoriosaka/rieki-hakken-2.git}"
BRANCH="${RIEKI2_BRANCH:-}"
APP_DIR="${RIEKI2_DIR:-$HOME/rieki-hakken-2}"
UV_DIR="${RIEKI2_UV_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${RIEKI2_DESKTOP:-$HOME/Desktop}"
SHORTCUT_NAME="利益発見ツール2"
PORT=8766
PY_VERSION="3.12"

export PATH="$UV_DIR:$PATH"

TOTAL=7

line() { echo "------------------------------------------------------------"; }
step() { echo; line; echo " $1/$TOTAL  $2"; line; }
ok()   { echo "  → OK: $1"; }

# 失敗したら原因と次にやることを表示して止める
fail() {
    echo
    echo "############################################################"
    echo "  セットアップを中断しました"
    echo "############################################################"
    echo "  原因: $1"
    echo
    echo "  次にやること:"
    shift
    for msg in "$@"; do echo "    ・$msg"; done
    echo
    echo "  分からないときは、この画面のスクリーンショットを送ってください。"
    echo
    pause_exit 1
}

pause_exit() {
    if [ -t 0 ] && [ "${RIEKI_NO_PAUSE:-}" != "1" ]; then
        read -r -p "Enter キーを押すとこの画面を閉じられます…" _
    fi
    exit "$1"
}

main() {
    echo
    echo "============================================================"
    echo "   利益発見ツール2  初回セットアップを始めます"
    echo "   （10〜20分ほどかかります。途中で操作することは基本的にありません）"
    echo "============================================================"

    step_git
    step_clone
    step_python
    step_playwright
    step_brands
    step_shortcut
    step_launch
}

# ---------------------------------------------------------------------
# 1/7 git
# ---------------------------------------------------------------------
step_git() {
    step 1 "git（Apple 純正の開発ツール）を確認しています…"
    if xcode-select -p >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
        ok "$(git --version)"
        return
    fi
    echo "  git がまだ入っていません。Apple のインストール画面を表示します。"
    xcode-select --install >/dev/null 2>&1
    fail "git（コマンドライン・デベロッパ・ツール）が入っていません。" \
         "画面に出た「コマンドライン・デベロッパ・ツールをインストールしますか？」で「インストール」を押してください。" \
         "利用許諾が出たら「同意する」を押します（10〜30分ほどかかります）。" \
         "「ソフトウェアがインストールされました」と出たら「完了」を押してください。" \
         "そのあと、このセットアップファイルをもう一度開いてください。" \
         "インストール画面が出ないときは、Mac を再起動してからもう一度開いてください。"
}

# ---------------------------------------------------------------------
# 2/7 ツール本体
# ---------------------------------------------------------------------
step_clone() {
    step 2 "ツール本体をダウンロードしています…（保存先: ${APP_DIR}）"
    if [ -d "$APP_DIR/.git" ]; then
        echo "  すでにダウンロード済みです。最新版に更新します…"
        if ! (cd "$APP_DIR" && GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet); then
            echo "  [注意] 最新版への更新はできませんでした。今の版のまま続けます（起動時にもう一度試します）。"
        fi
    elif [ -e "$APP_DIR" ]; then
        fail "$APP_DIR という名前のフォルダ（またはファイル）がすでにあり、ツールのフォルダではありません。" \
             "Finder でホームフォルダを開き、「rieki-hakken-2」の名前を「rieki-hakken-2-old」などに変えてください。" \
             "そのあと、このセットアップファイルをもう一度開いてください。"
    else
        # 公開リポジトリなので認証不要。GIT_TERMINAL_PROMPT=0 で、万一ログインを求められても入力待ちで止まらないようにする
        local bopt=()
        [ -n "$BRANCH" ] && bopt=(--branch "$BRANCH")
        if ! GIT_TERMINAL_PROMPT=0 git clone --quiet "${bopt[@]}" "$REPO_URL" "$APP_DIR"; then
            rm -rf "$APP_DIR"
            fail "ツール本体をダウンロードできませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。" \
                 "それでもだめなときは、この画面のスクリーンショットを紹介者に送ってください。"
        fi
    fi
    [ -f "$APP_DIR/start.command" ] && [ -f "$APP_DIR/requirements.txt" ] \
        || fail "ダウンロードしたツールのファイルが足りません。" "このセットアップファイルをもう一度開いてください。"
    chmod +x "$APP_DIR/start.command" "$APP_DIR/set_brands_password.command" 2>/dev/null
    # 知り合いモードの印（git 管理外）。起動時の自動更新で、誤って編集したファイルを退避してから更新する
    touch "$APP_DIR/.rieki_friend_mode"
    ok "ツール本体を準備しました"
}

# ---------------------------------------------------------------------
# 3/7 Python（uv）
# ---------------------------------------------------------------------
venv_ok() {
    [ -x "$APP_DIR/venv/bin/python" ] && \
        "$APP_DIR/venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

step_python() {
    step 3 "Python とライブラリを準備しています…（数分かかります）"
    if ! command -v uv >/dev/null 2>&1; then
        echo "  uv（Python の準備ツール）をダウンロードしています…"
        mkdir -p "$UV_DIR"
        if ! curl -LsSf --max-time 300 https://astral.sh/uv/install.sh \
                | env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1; then
            fail "uv をダウンロードできませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
        fi
        command -v uv >/dev/null 2>&1 || fail "uv を準備できませんでした。" "このセットアップファイルをもう一度開いてください。"
    fi
    ok "uv: $(uv --version)"

    cd "$APP_DIR" || fail "$APP_DIR を開けませんでした。" "このセットアップファイルをもう一度開いてください。"
    if venv_ok; then
        ok "Python は準備済みです（$(./venv/bin/python --version)）"
    else
        rm -rf ./venv
        echo "  Python ${PY_VERSION} を準備しています…"
        # --seed: venv の中に pip も入れておく（start.command が uv の無い環境でも pip で更新できるように）
        if ! uv venv --quiet --seed --python "$PY_VERSION" venv; then
            rm -rf ./venv
            fail "Python を準備できませんでした。" \
                 "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
        fi
        ok "Python を準備しました（$(./venv/bin/python --version)）"
    fi

    echo "  ライブラリをインストールしています…"
    if ! uv pip install --quiet --python ./venv/bin/python -r requirements.txt; then
        fail "ライブラリをインストールできませんでした。" \
             "インターネット接続を確認して、このセットアップファイルをもう一度開いてください。"
    fi
    # start.command が「requirements.txt が変わったか」を判定するための印
    shasum -a 256 requirements.txt | awk '{print $1}' > ./venv/.rieki_requirements.sha256
    ok "ライブラリをインストールしました"
}

# ---------------------------------------------------------------------
# 4/7 Chromium
# ---------------------------------------------------------------------
step_playwright() {
    step 4 "ブラウザ部品（Chromium）を準備しています…（数分かかります）"
    cd "$APP_DIR" || exit 1
    if [ "${RIEKI_SKIP_PLAYWRIGHT:-}" = "1" ]; then
        echo "  （テスト: Chromium のインストールは省略）"
        return
    fi
    if ! ./venv/bin/playwright install chromium; then
        fail "ブラウザ部品（Chromium）をインストールできませんでした。" \
             "インターネット接続と、Mac の空き容量（1GB 以上）を確認してください。" \
             "そのあと、このセットアップファイルをもう一度開いてください。"
    fi
    ./venv/bin/python -c 'import importlib.metadata as m; print(m.version("playwright"))' \
        > ./venv/.rieki_playwright_version 2>/dev/null
    ok "Chromium を準備しました"
}

# ---------------------------------------------------------------------
# 5/7 ブランド一覧（物販ONEのブランド一覧。平文の brands.json。無ければ暗号化ファイル brands.enc）
# ---------------------------------------------------------------------
step_brands() {
    step 5 "ブランド一覧を準備しています…"
    cd "$APP_DIR" || exit 1
    if [ -f brands.json ]; then
        # 平文方式: パスワード不要。まだ取り込んでいないブランドだけ追加
        ./venv/bin/python brands_sync.py --auto
        ok "ブランド一覧を準備しました"
        return
    fi
    if [ ! -f brands.enc ]; then
        echo "  （ブランド一覧のファイルがまだありません。飛ばします）"
        return
    fi
    if [ -s .brands_password ] && ./venv/bin/python brands_sync.py --check >/dev/null 2>&1; then
        ./venv/bin/python brands_sync.py --auto
        ok "ブランド一覧のパスワードは入力済みです"
        return
    fi
    echo "  紹介者から聞いた「ブランド一覧のパスワード」を入れる画面を出します。"
    echo "  （分からないときは「あとで」を押してください。ツールはそのまま使えます）"
    bash ./set_brands_password.command
    if [ -s .brands_password ]; then
        ok "ブランド一覧を取り込みました"
    else
        echo "  → ブランド一覧はまだ取り込んでいません（あとから入れられます。次にツールを起動したときにも聞きます）"
    fi
}

# ---------------------------------------------------------------------
# 6/7 デスクトップの起動用ファイル
# ---------------------------------------------------------------------
step_shortcut() {
    step 6 "デスクトップに「${SHORTCUT_NAME}」を作っています…"
    mkdir -p "$DESKTOP_DIR"
    local f="$DESKTOP_DIR/$SHORTCUT_NAME.command"
    # 自分の Mac で作ったファイルなので、ダブルクリックでそのまま開ける（Gatekeeper の警告は出ない）
    cat > "$f" <<EOF
#!/bin/bash
# 利益発見ツール2 を起動します（ダブルクリックで開いてください）
exec "$APP_DIR/start.command"
EOF
    chmod +x "$f" || fail "デスクトップにファイルを作れませんでした。" "このセットアップファイルをもう一度開いてください。"
    # アイコンを付ける（失敗しても続行。見た目だけの処理）
    if [ -f "$APP_DIR/assets/icon.icns" ]; then
        osascript -l JavaScript -e 'ObjC.import("AppKit"); function run(a){ var i=$.NSImage.alloc.initWithContentsOfFile(a[0]); if(!i.isNil()) $.NSWorkspace.sharedWorkspace.setIconForFileOptions(i, a[1], 0); }' \
            "$APP_DIR/assets/icon.icns" "$f" >/dev/null 2>&1 || true
    fi
    ok "デスクトップに「$SHORTCUT_NAME.command」を作りました。次回からはこれをダブルクリックします"
}

# ---------------------------------------------------------------------
# 7/7 起動
# ---------------------------------------------------------------------
step_launch() {
    step 7 "ツールを起動しています…"
    echo
    echo "  ★ セットアップが完了しました！"
    echo "    まもなくブラウザでリサーチ画面（http://127.0.0.1:${PORT}）が開きます。"
    echo "    使っている間は、この黒い画面を閉じないでください。"
    echo "    終わるときは、この画面を閉じれば OK です。"
    echo "    次回からは、デスクトップの「${SHORTCUT_NAME}」をダブルクリックしてください。"
    echo
    if [ "${RIEKI_NO_LAUNCH:-}" = "1" ]; then
        echo "  （テスト: 起動は省略）"
        exit 0
    fi
    exec "$APP_DIR/start.command"
}

main "$@"; exit $?
