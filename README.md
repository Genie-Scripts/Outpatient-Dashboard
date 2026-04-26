# 外来効率化ダッシュボード（医師フィードバック側）

東京医療センターの外来部門向け、医師・看護師の **自己点検用** 静的HTMLダッシュボード。
匿名IDで自分の指標を確認し、現場主導の改善（再診の見直し・診察時間調整・紹介率改善）に
活用するためのフィードバックサイト。

> **経営計画的な再編分析** は別リポ
> [Outpatient-Restructuring](https://github.com/Genie-Scripts/Outpatient-Restructuring)
> （[公開URL](https://genie-scripts.github.io/Outpatient-Restructuring/)）に分離されています。
> 予約枠サマリ・曜日×時間帯／医師×時間帯／外来枠×時間帯ヒートマップは Restructuring 側で
> より高機能（4区分×3指標、並び順・ハイライト切替）にリニューアル済みです。

---

## 機能概要

| ダッシュボード | 出力先 | 用途 |
|---|---|---|
| 月次管理 | `docs/monthly/YYYY-MM.html` | 全科KPI・6ヶ月トレンド・LLMハイライト |
| 診療科深掘り | `docs/dept/YYYY-MM/<CODE>.html` | 自科の時間帯分布・医師別内訳・逆紹介候補 |
| 医師別分析 | `docs/doctor_analysis.html` | 自分の匿名IDで初診・再診・紹介率を確認 |
| 薬再診候補スコア | `docs/drug_revisit.html` | 自分の薬再診比率の自己点検 |
| ハブ | `docs/index.html` | サイト内リンク集＋経営企画サイトへの導線 |

---

## 二系統リポジトリの役割分担

```
                  ┌─ data/aggregated/ (本リポ生成・両方が消費) ─┐
                  │                                             │
  data/raw/  ──>  本リポ  ──> docs/ (現場医師向け)               │
   (生データ)     ↑ aggregate.py                                │
                  │                                             │
                  └─ Outpatient-Restructuring (経営企画向け) ────┘
                      └ docs/ (4テーマ・5ヒートマップ)
```

- **本リポ（Outpatient-Dashboard）**: 現場医師・看護師の自己点検用
  - 月次管理／診療科深掘り／医師別分析／薬再診
  - 集計CSV (`data/aggregated/`) の **生成元**
- **Outpatient-Restructuring**: 経営企画室の意思決定支援用
  - 紹介・逆紹介／予約枠サマリ／曜日×時間帯／医師×時間帯／外来枠×時間帯
  - 本リポの集計CSVを読み取って動作する **コンシューマ**

---

## セットアップ

```bash
# Python 3.11+ が必要
cd ~/dev/ai-apps/Outpatient-Dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## 月次運用フロー（標準）

```bash
# 1. 電子カルテからエクスポートしたCSVを data/raw/ に置く
cp 外来データ_2026-05.csv data/raw/

# 2. 一括実行（これだけでOK）
python -m src.cli run-all --no-llm

# 3. docs/ 以下の HTML を確認 → git push で公開
#    ※ data/aggregated/ 配下も commit 必須（Restructuring が読む）
```

`--no-llm` を外すと LM Studio（ローカルLLM）でハイライト文章を自動生成します（要起動）。

---

## コマンドリファレンス

### `anonymize` — 匿名化

```
python -m src.cli anonymize [--month YYYY-MM]
```

- 出力: `data/raw/anonymized/raw_data_YYYY-MM.csv`
- 医師実名 → `DR_<科コード><3桁連番>`（対応表: `config/master_key.csv`）
- 予約名称 → `SL_<4桁連番>`（対応表: `config/slot_key.csv`）
- 未登録の医師・予約名称は自動追記

### `aggregate` — 集計

```
python -m src.cli aggregate [--month YYYY-MM]
```

- 出力: `data/aggregated/YYYY-MM/`（12ファイル）
- このCSV群を **Outpatient-Restructuring 側も読む** ため、コミット忘れに注意

### `build` — ダッシュボード生成

```
python -m src.cli build monthly      [--month YYYY-MM] [--no-llm]
python -m src.cli build dept         [--month YYYY-MM]
python -m src.cli build doctor       [--month YYYY-MM]    # 省略時は最新月
python -m src.cli build drug-revisit [--month YYYY-MM]    # 省略時は最新月
python -m src.cli build hub
```

### `run-all` — 全工程一括

```
python -m src.cli run-all [--month YYYY-MM] [--no-llm]
```

`anonymize → aggregate → build monthly → build dept → build doctor → build drug-revisit → build hub`
を順に実行。

### グローバルオプション

| オプション | 説明 |
|---|---|
| `--no-llm` | LLM未使用（定型文でハイライト生成） |
| `--verbose` | DEBUG レベルの詳細ログを表示 |

---

## ディレクトリ構成

```
Outpatient-Dashboard/
├── src/
│   ├── anonymize.py           # 医師名・予約名称 → 匿名ID変換
│   ├── aggregate.py           # 匿名化済みCSV → 集計CSV（両リポが消費）
│   ├── cli.py                 # CLI統合エントリポイント
│   ├── llm_client.py          # LM Studio クライアント
│   ├── core/                  # 分類・読込・採点・ハイライト抽出
│   └── dashboards/
│       ├── monthly.py         # 月次管理
│       ├── dept_drilldown.py  # 診療科深掘り
│       ├── doctor_analysis.py # 医師別深掘り
│       ├── drug_revisit.py    # 薬再診候補スコア
│       └── hub.py             # ハブページ
│
├── templates/                 # Jinja2 HTML テンプレート
├── static/css|js              # ウォームライトテーマ・共通JS
├── config/                    # 分類・目標・LLM設定・匿名対応表（後者2つはGitignore）
├── data/
│   ├── raw/                   # ⛔ Gitignore: 生データ
│   │   └── anonymized/        # ⛔ Gitignore: 匿名化済み中間データ
│   └── aggregated/YYYY-MM/    # ✓ Commit 可: 集計済み（Restructuring が読む）
│
├── docs/                      # GitHub Pages 公開物
│   ├── index.html             # ハブ
│   ├── monthly/YYYY-MM.html
│   ├── dept/YYYY-MM/<CODE>.html
│   ├── doctor_analysis.html
│   └── drug_revisit.html
│
└── tests/                     # pytest テスト
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

1. LM Studio を起動し、`Qwen2.5-14B-Instruct (Q4_K_M)` 等を読み込む
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
