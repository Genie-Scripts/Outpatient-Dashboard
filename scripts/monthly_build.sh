#!/usr/bin/env bash
# 月次一括実行: data/raw/raw_data_$1.csv を匿名化→集計→月次ダッシュボード生成
# 使い方: ./scripts/monthly_build.sh 2026-04
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "使い方: $0 YYYY-MM [--no-llm]" >&2
  exit 1
fi

MONTH="$1"
shift
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

python -m src.cli run-all --month "$MONTH" "$@"
