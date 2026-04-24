# 外来効率化ダッシュボード

東京医療センター経営企画室向け、外来の構造改革（平準化・逆紹介推進・初診増加）を
データ駆動で推進する月次管理ダッシュボードシステム。

---

## 機能概要

| ダッシュボード | 出力先 | 内容 |
|---|---|---|
| 月次管理 | `docs/monthly/YYYY-MM.html` | 全科KPI・6ヶ月トレンド・LLMハイライト |
| 診療科深掘り | `docs/dept/YYYY-MM/<CODE>.html` | 時間帯分布・医師別内訳・逆紹介候補 |
| 予約枠再設計 | `docs/slot_redesign.html` | 稀用枠・命名乖離・A枠候補の一覧 |
| 医師別分析 | `docs/doctor_analysis.html` | 匿名医師ごとの初診・再診・紹介率 |
| ハブ | `docs/index.html` | 全ダッシュボードへのリンク集 |

---

## セットアップ

```bash
# Python 3.11+ が必要
cd ~/dev/ai-apps/Outpatient
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## 月次運用フロー（標準）

```bash
# 1. 電子カルテからエクスポートしたCSVを data/raw/ に置く
#    ファイル名は任意（複数月・複数ファイルも可）
cp 外来データ_2026-05.csv data/raw/

# 2. 一括実行（これだけでOK）
python -m src.cli run-all --no-llm

# 3. docs/ 以下の HTML を確認 → git push で公開
```

`--no-llm` を外すと LM Studio（ローカルLLM）でハイライト文章を自動生成します（要起動）。

---

## コマンドリファレンス

### `anonymize` — 匿名化

```
python -m src.cli anonymize [--month YYYY-MM]
```

| オプション | 説明 |
|---|---|
| （省略）| `data/raw/` の全CSVを自動スキャン・マージし月別に分割出力 |
| `--month YYYY-MM` | 指定月の単一ファイル（`raw_data_YYYY-MM.csv`）のみ処理 |

- 出力: `data/raw/anonymized/raw_data_YYYY-MM.csv`
- 医師実名 → `DR_<科コード><3桁連番>`（対応表: `config/master_key.csv`）
- 予約名称 → `SL_<4桁連番>`（対応表: `config/slot_key.csv`）
- 未登録の医師・予約名称は自動追記

---

### `aggregate` — 集計

```
python -m src.cli aggregate [--month YYYY-MM]
```

- 省略時: `data/raw/anonymized/` の全月を処理
- 出力: `data/aggregated/YYYY-MM/`（12ファイル）

---

### `build` — ダッシュボード生成

```
python -m src.cli build monthly [--month YYYY-MM] [--no-llm]
python -m src.cli build dept    [--month YYYY-MM]
python -m src.cli build slot    [--month YYYY-MM]    # 省略時は最新月
python -m src.cli build doctor  [--month YYYY-MM]    # 省略時は最新月
python -m src.cli build hub
```

---

### `run-all` — 全工程一括

```
python -m src.cli run-all [--month YYYY-MM] [--no-llm]
```

`anonymize → aggregate → build monthly → build dept → build slot → build doctor → build hub`
を順に実行。`--month` 省略時は `data/raw/` を自動スキャン。

---

### グローバルオプション

| オプション | 説明 |
|---|---|
| `--no-llm` | LLM未使用（定型文でハイライト生成） |
| `--verbose` | DEBUG レベルの詳細ログを表示 |

---

## ディレクトリ構成

```
Outpatient/
├── src/
│   ├── anonymize.py           # 医師名・予約名称 → 匿名ID変換
│   ├── aggregate.py           # 匿名化済みCSV → 12種の集計CSV
│   ├── cli.py                 # CLI統合エントリポイント
│   ├── llm_client.py          # LM Studio クライアント
│   ├── core/
│   │   ├── classify.py        # 診療科分類参照API
│   │   ├── data_loader.py     # 集計CSV読込・直近N ヶ月取得
│   │   ├── grading.py         # 達成率 → S/A/B/C/D 評価
│   │   └── highlights.py      # ハイライト候補抽出（LLM入力用）
│   └── dashboards/
│       ├── monthly.py         # 月次管理ダッシュボード
│       ├── dept_drilldown.py  # 診療科深掘り（全科一括）
│       ├── slot_redesign.py   # 予約枠再設計提案
│       ├── doctor_analysis.py # 医師別深掘り
│       └── hub.py             # ハブページ（docs/index.html）
│
├── templates/                 # Jinja2 HTML テンプレート
│   ├── base.html
│   ├── monthly.html
│   ├── dept_drilldown.html
│   ├── slot_redesign.html
│   ├── doctor_analysis.html
│   └── index.html
│
├── static/
│   ├── css/theme.css          # ウォームライトテーマ
│   └── js/common.js           # 共通ユーティリティ・Chart.js 設定
│
├── config/
│   ├── dept_classification.csv  # 診療科分類（タイプ・コード・評価対象）
│   ├── dept_targets.csv         # 診療科別月次目標値
│   ├── llm_config.yaml          # LM Studio 接続設定
│   ├── master_key.csv           # ⛔ Gitignore: 医師名↔匿名ID対応表
│   └── slot_key.csv             # ⛔ Gitignore: 予約名称↔匿名ID対応表
│
├── data/
│   ├── raw/                     # ⛔ Gitignore: 生データ（患者情報含む）
│   │   └── anonymized/          # 匿名化済み（Gitignore内のため非公開）
│   └── aggregated/YYYY-MM/      # 集計済みCSV（コミット可）
│
├── docs/                        # GitHub Pages 公開物
│   ├── index.html               # ハブページ
│   ├── monthly/YYYY-MM.html
│   ├── dept/YYYY-MM/<CODE>.html
│   ├── slot_redesign.html
│   └── doctor_analysis.html
│
├── tests/                       # pytest テスト（26ケース）
└── scripts/
    ├── monthly_build.sh
    └── deploy.sh
```

---

## 設定ファイル

### `config/dept_classification.csv`

診療科の分類・コード・評価対象フラグを管理。

```csv
診療科名,タイプ,診療科コード,表示順,評価対象,備考
泌尿器科,外科系,U,1,TRUE,
眼科,外科系,E,2,TRUE,
入退院支援センター,その他,AD,18,FALSE,運用系
```

### `config/dept_targets.csv`

診療科別の月次目標値。省略時は過去実績から自動算出。

```csv
診療科名,初診目標_月,薬のみ再診_目標,再診初診比率_目標
泌尿器科,120,600,20.0
```

### `config/llm_config.yaml`

```yaml
endpoint: http://localhost:1234/v1/chat/completions
model: local-model          # LM Studio でロードしたモデル名
temperature: 0.3
max_tokens: 500
timeout: 120
```

---

## セキュリティ

| ルール | 理由 |
|---|---|
| `data/raw/` を絶対にコミットしない | 患者情報を含む生データ |
| `config/master_key.csv` をコミットしない | 医師実名↔匿名IDの対応表 |
| `config/slot_key.csv` をコミットしない | 予約名称↔匿名IDの対応表 |
| コミット前に `git status` で確認 | 誤コミット防止 |

`docs/` 配下には匿名化済み集計データのみ含まれるため、
GitHub Pages で公開しても患者・医師の個人情報は流出しません。

---

## LM Studio 設定（オプション）

1. LM Studio を起動し、`Qwen2.5-14B-Instruct (Q4_K_M)` を読み込む
2. ローカルサーバを `http://localhost:1234` で起動
3. `config/llm_config.yaml` のモデル名を一致させる
4. `--no-llm` なしで実行

LM Studio が起動していない場合は自動でフォールバック定型文が使われます。

---

## 仕様書

- `spec/spec_v0.2.md`：設計仕様書（ローカル参照専用、`.gitignore` 対象）
- `CLAUDE.md`：Claude Code 向け実装ガイドライン

---

## ライセンス

院内利用専用（非公開）。
