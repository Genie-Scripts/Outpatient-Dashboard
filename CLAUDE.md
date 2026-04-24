# Claude Code への指示

## このプロジェクトは何か

東京医療センター経営企画室のための、外来効率化ダッシュボードシステム。
月次データから経営指標を計算し、静的HTMLで可視化する。

## 必ず守るべき原則

### 1. データ取り扱い

- **`data/raw/` はGitコミット厳禁**（生の患者データ）
- **`config/master_key.csv` もGitコミット厳禁**（医師実名の対応表）
- 新しいファイルを作る前に、`.gitignore` に含まれているか必ず確認
- コミット前に `git status` で `data/raw/*` や `master_key` が含まれていないか確認

### 2. LLMには計算をさせない

- 数値処理・集計・評価は全て Python で実装
- LLMはハイライトの文章生成のみに使う
- LLMに渡すのは「Python が計算済みの数値」であり、「LLMに計算させる生データ」ではない
- 理由：LLMの幻覚による数値誤りを排除するため

### 3. 疎結合アーキテクチャ

- 集計CSVを標準インターフェースとする
- 各ダッシュボード生成モジュールは集計CSVからのみデータを取る
- 生データCSVへの直接アクセスは `src/anonymize.py` と `src/aggregate.py` のみ

### 4. 静的HTML出力

- Jinja2テンプレートで生成
- Chart.js でグラフ描画（CDN経由）
- 複雑なJavaScriptフレームワーク不使用
- サーバ不要、GitHub Pagesで完結

### 5. 匿名化の一貫性

- 医師の匿名IDは `config/master_key.csv` で管理
- 同じ医師には常に同じ匿名IDが振られる
- 新規医師が出現したら自動追記
- 匿名IDは `DR_<診療科コード><3桁連番>` 形式（例：DR_U001）

## コーディング規約

- Python 3.11+、型ヒント必須
- docstring は Google スタイル
- Ruff でリント、Black でフォーマット
- 関数は単一責務、1関数50行以内を目安
- モジュール名・関数名は英語、コメント・docstringは日本語OK

## ディレクトリ構成の原則

```
src/            # ロジック本体
templates/      # HTMLテンプレート（Jinja2）
static/         # 共通CSS/JSアセット
config/         # 設定ファイル
data/           # データ（data/raw のみGitignore、data/aggregatedはコミット可）
docs/           # GitHub Pages公開ディレクトリ
tests/          # テスト
scripts/        # 運用スクリプト
```

## テストの書き方

- `tests/` 配下に pytest でテスト
- フィクスチャは `tests/fixtures/` に小さなCSVを置く
- コアロジック（grading, highlights, classify, anonymize）は80%以上カバレッジ

## 実装の優先順位（Phase 1）

1. `.gitignore` と `pyproject.toml` を最初に整備
2. `config/dept_classification.csv` と `config/master_key.csv` 雛形を準備
3. `src/anonymize.py` から実装（後続すべての前提）
4. `src/aggregate.py` 実装
5. `src/core/` 実装
6. `src/dashboards/monthly.py` 実装
7. `src/cli.py` で統合
8. テストを並行して追加

## 参考資料の場所

- 仕様書: `spec/spec_v0.2.md`（リポジトリ直上の親ディレクトリに配置、.gitignore対象）

## 変更時の注意

- `config/dept_targets.csv` を編集する場合は、背景を commit メッセージに明記
- `templates/` の変更は必ず全ダッシュボードの表示確認
- 本番生成前に `--no-llm` モードで動作確認
- master_key の命名規則を変更する場合は、既存データへの影響を明記
