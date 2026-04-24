#!/bin/bash
# local_build.sh — 実名表示版ダッシュボードをローカル生成する（Gitコミット対象外）
#
# 使い方:
#   ./scripts/local_build.sh             # 月引数なし（data/raw/ を自動スキャン）
#   ./scripts/local_build.sh 2026-04     # 指定月のみ
#
# 出力先: local/docs/（.gitignore 対象 — 絶対にコミットしない）
# 注意: 医師実名・予約枠名が HTML に含まれます。院外共有禁止。
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

LOG="/tmp/outpatient_local_build.log"

log() {
  echo "$@" | tee -a "$LOG"
}

notify() {
  osascript -e "display notification \"$1\" with title \"外来ダッシュボード（実名版）\" subtitle \"$2\"" 2>/dev/null || true
}

error_dialog() {
  osascript -e "display dialog \"$1\" buttons {\"OK\"} with title \"エラー\" with icon caution" 2>/dev/null || true
  log "❌ $1"
}

trap 'error_dialog "予期せぬエラーで停止しました。詳細は $LOG を確認してください。"' ERR

log "=== $(date '+%Y/%m/%d %H:%M:%S') local_build 開始（実名モード）==="
notify "実名版ビルドを開始しました。" "🔒 local_build 開始"

# ── 0. リポジトリルートへ移動 & 仮想環境有効化 ──
cd "$(dirname "$0")/.."

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  log "✅ 仮想環境を有効化"
else
  error_dialog "仮想環境(.venv)が見つかりません。python -m venv .venv で作成してください。"
  exit 1
fi

# ── 1. local/ が .gitignore 対象であることを確認 ──
if ! git check-ignore -q local/ 2>/dev/null; then
  error_dialog "local/ が .gitignore に登録されていません。中断します。"
  exit 1
fi

# ── 2. run-all --no-anon ──
log "🔨 実名版ビルド中... (出力先: local/docs/)"
notify "ビルド中..." "run-all --no-anon"

if [ $# -ge 1 ]; then
  if ! python -m src.cli run-all --no-anon --no-llm --month "$1" 2>&1 | tee -a "$LOG"; then
    error_dialog "run-all --no-anon に失敗しました。$LOG を確認してください。"
    exit 1
  fi
else
  if ! python -m src.cli run-all --no-anon --no-llm 2>&1 | tee -a "$LOG"; then
    error_dialog "run-all --no-anon に失敗しました。$LOG を確認してください。"
    exit 1
  fi
fi

log "✅ ビルド完了"
notify "実名版ダッシュボードの生成が完了しました。" "✅ ビルド完了"

# ── 3. ブラウザで開く ──
INDEX="local/docs/index.html"
if [ -f "$INDEX" ]; then
  log "🌐 ブラウザで開きます: $INDEX"
  open "$INDEX"
fi

log "=== $(date '+%Y/%m/%d %H:%M:%S') local_build 完了 ==="
log "📁 出力先: $(pwd)/local/docs/"
log "⚠️  このディレクトリは院外共有禁止です。"
