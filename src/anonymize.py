"""医師実名・予約名称→匿名ID変換モジュール。

入力: data/raw/ 配下の任意CSV（複数可）
出力: data/raw/anonymized/raw_data_YYYY-MM.csv（月別）

処理:
    1. 「予約担当者名」→ DR_<診療科コード><3桁連番>（config/master_key.csv）
    2. 「予約名称」     → SL_<4桁連番>              （config/slot_key.csv）
    3. どちらの対応表もリポジトリには絶対にコミットしない
    4. 匿名化済みCSVを月別に分割出力
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

DATE_COLUMN = "予約日"

logger = logging.getLogger(__name__)

SOURCE_COLUMN = "予約担当者名"
TARGET_COLUMN = "予約担当者匿名ID"
DEPT_COLUMN = "診療科名"
SLOT_SOURCE_COLUMN = "予約名称"

MASTER_KEY_COLUMNS = ["実名", "匿名ID", "診療科名", "初回登録日", "備考"]
SLOT_KEY_COLUMNS = ["予約名称", "匿名ID", "初回登録日"]
DEFAULT_DEPT_CODE = "XX"


@dataclass
class AnonymizationResult:
    """匿名化実行結果のサマリ。"""

    input_path: Path
    output_path: Path
    total_rows: int
    unique_names_total: int
    newly_registered: list[tuple[str, str, str]] = field(default_factory=list)
    """(実名, 匿名ID, 診療科名) の新規登録ログ。"""


def _read_csv_auto_encoding(path: Path) -> pd.DataFrame:
    """UTF-8-SIG / CP932 を自動判別して読み込む。"""
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp932")


def _load_dept_code_map(dept_classification_path: Path) -> dict[str, str]:
    """診療科名→診療科コード辞書を返す。"""
    df = pd.read_csv(dept_classification_path, encoding="utf-8-sig")
    return dict(zip(df["診療科名"].astype(str), df["診療科コード"].astype(str)))


def _load_master_key(master_key_path: Path) -> pd.DataFrame:
    """master_key.csv を読み込む。存在しなければヘッダのみのDataFrameを返す。"""
    if not master_key_path.exists():
        return pd.DataFrame(columns=MASTER_KEY_COLUMNS)

    df = pd.read_csv(master_key_path, encoding="utf-8-sig")
    for col in MASTER_KEY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[MASTER_KEY_COLUMNS]


def _load_slot_key(slot_key_path: Path) -> pd.DataFrame:
    """slot_key.csv を読み込む。存在しなければヘッダのみのDataFrameを返す。"""
    if not slot_key_path.exists():
        return pd.DataFrame(columns=SLOT_KEY_COLUMNS)
    df = pd.read_csv(slot_key_path, encoding="utf-8-sig")
    for col in SLOT_KEY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[SLOT_KEY_COLUMNS]


def _anonymize_slot_names(
    df: pd.DataFrame,
    slot_key_path: Path,
    today: str,
) -> pd.DataFrame:
    """予約名称列の値を SL_<4桁連番> に変換して slot_key.csv を更新する。"""
    if SLOT_SOURCE_COLUMN not in df.columns:
        return df

    slot_df = _load_slot_key(slot_key_path)
    existing: dict[str, str] = dict(
        zip(slot_df["予約名称"].astype(str), slot_df["匿名ID"].astype(str))
    )

    next_serial = max(
        (int(v[3:]) for v in existing.values() if v.startswith("SL_") and v[3:].isdigit()),
        default=0,
    ) + 1

    name_to_id: dict[str, str] = dict(existing)
    new_rows: list[dict] = []

    for raw_name in df[SLOT_SOURCE_COLUMN].dropna().unique():
        raw_name = str(raw_name)
        if raw_name in name_to_id:
            continue
        anon_id = f"SL_{next_serial:04d}"
        next_serial += 1
        name_to_id[raw_name] = anon_id
        new_rows.append({"予約名称": raw_name, "匿名ID": anon_id, "初回登録日": today})
        logger.info("予約名称 新規登録: %s → %s", raw_name, anon_id)

    if new_rows:
        slot_df = pd.concat(
            [slot_df, pd.DataFrame(new_rows, columns=SLOT_KEY_COLUMNS)],
            ignore_index=True,
        )
        slot_key_path.parent.mkdir(parents=True, exist_ok=True)
        slot_df.to_csv(slot_key_path, index=False, encoding="utf-8-sig")

    df = df.copy()
    df[SLOT_SOURCE_COLUMN] = df[SLOT_SOURCE_COLUMN].map(name_to_id)
    return df


def _next_serial(master_df: pd.DataFrame, dept_code: str) -> int:
    """診療科コードに対応する次の連番を返す。"""
    prefix = f"DR_{dept_code}"
    used: list[int] = []
    for anon_id in master_df["匿名ID"].dropna().astype(str):
        if anon_id.startswith(prefix):
            suffix = anon_id[len(prefix):]
            if suffix.isdigit():
                used.append(int(suffix))
    return max(used, default=0) + 1


def _assign_new_id(
    master_df: pd.DataFrame,
    real_name: str,
    dept_name: str,
    dept_code_map: dict[str, str],
    today: str,
) -> tuple[pd.DataFrame, str]:
    """未登録の実名に匿名IDを払い出し、master_dfに追記して返す。"""
    dept_code = dept_code_map.get(dept_name, DEFAULT_DEPT_CODE)
    serial = _next_serial(master_df, dept_code)
    anon_id = f"DR_{dept_code}{serial:03d}"
    new_row = pd.DataFrame(
        [{
            "実名": real_name,
            "匿名ID": anon_id,
            "診療科名": dept_name,
            "初回登録日": today,
            "備考": "",
        }],
        columns=MASTER_KEY_COLUMNS,
    )
    master_df = pd.concat([master_df, new_row], ignore_index=True)
    return master_df, anon_id


@dataclass
class DirectoryAnonymizationResult:
    """ディレクトリ一括匿名化の結果サマリ。"""

    input_files: list[Path]
    months: list[str]
    total_rows: int
    newly_registered: list[tuple[str, str, str]] = field(default_factory=list)


def _anonymize_df(
    df: pd.DataFrame,
    master_key_path: Path,
    dept_classification_path: Path,
    slot_key_path: Path,
    today: str,
) -> tuple[pd.DataFrame, list[tuple[str, str, str]]]:
    """DataFrameを受け取り匿名化済みDataFrameと新規登録ログを返す。"""
    if SOURCE_COLUMN not in df.columns:
        raise ValueError(f"入力データに列 '{SOURCE_COLUMN}' がありません")
    if DEPT_COLUMN not in df.columns:
        raise ValueError(f"入力データに列 '{DEPT_COLUMN}' がありません")

    dept_code_map = _load_dept_code_map(dept_classification_path)
    master_df = _load_master_key(master_key_path)
    existing_map = dict(zip(master_df["実名"].astype(str), master_df["匿名ID"].astype(str)))

    unique_pairs = (
        df[[SOURCE_COLUMN, DEPT_COLUMN]].dropna(subset=[SOURCE_COLUMN]).drop_duplicates()
    )

    newly_registered: list[tuple[str, str, str]] = []
    name_to_id: dict[str, str] = dict(existing_map)

    for real_name, dept_name in unique_pairs.itertuples(index=False):
        real_name = str(real_name)
        dept_name = str(dept_name) if pd.notna(dept_name) else ""
        if real_name in name_to_id:
            continue
        master_df, anon_id = _assign_new_id(
            master_df, real_name, dept_name, dept_code_map, today
        )
        name_to_id[real_name] = anon_id
        newly_registered.append((real_name, anon_id, dept_name))
        logger.info("新規登録: %s → %s (%s)", real_name, anon_id, dept_name)

    df = df.copy()
    df[TARGET_COLUMN] = df[SOURCE_COLUMN].map(name_to_id)
    df = df.drop(columns=[SOURCE_COLUMN])

    master_key_path.parent.mkdir(parents=True, exist_ok=True)
    master_df.to_csv(master_key_path, index=False, encoding="utf-8-sig")

    # 予約名称の匿名化
    df = _anonymize_slot_names(df, slot_key_path, today)

    return df, newly_registered


def anonymize_monthly_data(
    input_path: Path,
    output_path: Path,
    master_key_path: Path,
    dept_classification_path: Path,
    slot_key_path: Path | None = None,
    today: str | None = None,
) -> AnonymizationResult:
    """月次生データを読み、医師実名・予約名称を匿名IDに変換して出力する。

    Args:
        input_path: 生データCSV（data/raw/raw_data_YYYY-MM.csv）
        output_path: 匿名化済み出力先（data/raw/anonymized/raw_data_YYYY-MM.csv）
        master_key_path: 医師対応表（config/master_key.csv）
        dept_classification_path: 診療科分類（config/dept_classification.csv）
        slot_key_path: 予約名称対応表（config/slot_key.csv）。Noneなら master_key と同ディレクトリ。
        today: 初回登録日として使う日付（YYYY-MM-DD）。Noneなら実行日。

    Returns:
        AnonymizationResult
    """
    today = today or date.today().isoformat()
    slot_key_path = slot_key_path or master_key_path.parent / "slot_key.csv"
    logger.info("匿名化開始: %s", input_path)
    df = _read_csv_auto_encoding(input_path)

    df, newly_registered = _anonymize_df(
        df, master_key_path, dept_classification_path, slot_key_path, today
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    result = AnonymizationResult(
        input_path=input_path,
        output_path=output_path,
        total_rows=len(df),
        unique_names_total=df[TARGET_COLUMN].nunique(),
        newly_registered=newly_registered,
    )
    logger.info(
        "匿名化完了: %d行 / ユニーク医師 %d名 / 新規 %d名",
        result.total_rows, result.unique_names_total, len(newly_registered),
    )
    return result


def passthrough_monthly_data(
    input_path: Path,
    output_path: Path,
) -> AnonymizationResult:
    """匿名化をスキップし、列名変換のみ行う。ローカル確認専用。

    master_key.csv / slot_key.csv は更新しない。
    """
    df = _read_csv_auto_encoding(input_path)
    if SOURCE_COLUMN not in df.columns:
        raise ValueError(f"入力データに列 '{SOURCE_COLUMN}' がありません")
    df = df.copy().rename(columns={SOURCE_COLUMN: TARGET_COLUMN})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return AnonymizationResult(
        input_path=input_path,
        output_path=output_path,
        total_rows=len(df),
        unique_names_total=df[TARGET_COLUMN].nunique(),
    )


def passthrough_directory(
    raw_dir: Path,
    output_dir: Path,
) -> DirectoryAnonymizationResult:
    """raw_dir 内の全CSVをマージして列名変換のみ行い、月別に分割出力する。ローカル確認専用。"""
    csv_files = [f for f in sorted(raw_dir.glob("*.csv")) if f.is_file()]
    if not csv_files:
        raise FileNotFoundError(f"CSVファイルが見つかりません: {raw_dir}")

    frames = [_read_csv_auto_encoding(f) for f in csv_files]
    merged = pd.concat(frames, ignore_index=True)
    if SOURCE_COLUMN not in merged.columns:
        raise ValueError(f"入力データに列 '{SOURCE_COLUMN}' がありません")

    df = merged.copy().rename(columns={SOURCE_COLUMN: TARGET_COLUMN})
    dates = pd.to_datetime(df[DATE_COLUMN], errors="coerce")
    df["_month"] = dates.dt.to_period("M").astype(str)

    output_dir.mkdir(parents=True, exist_ok=True)
    months: list[str] = []
    for month, group in df.groupby("_month"):
        month = str(month)
        if month == "NaT":
            logger.warning("日付不正な %d 行をスキップ", len(group))
            continue
        out_path = output_dir / f"raw_data_{month}.csv"
        group.drop(columns=["_month"]).to_csv(out_path, index=False, encoding="utf-8-sig")
        months.append(month)

    return DirectoryAnonymizationResult(
        input_files=csv_files,
        months=sorted(months),
        total_rows=len(df),
    )


def anonymize_directory(
    raw_dir: Path,
    output_dir: Path,
    master_key_path: Path,
    dept_classification_path: Path,
    slot_key_path: Path | None = None,
    today: str | None = None,
) -> DirectoryAnonymizationResult:
    """raw_dir 内の全CSVをマージして匿名化し、月別に分割出力する。

    サブディレクトリ（anonymized/ など）は走査しない。
    同じ月のデータが複数ファイルに分散していても合算される。

    Args:
        raw_dir: 生データCSVを置くディレクトリ（data/raw/）
        output_dir: 匿名化済みCSVの出力先（data/raw/anonymized/）
        master_key_path: 医師対応表（config/master_key.csv）
        dept_classification_path: 診療科分類（config/dept_classification.csv）
        slot_key_path: 予約名称対応表（config/slot_key.csv）。Noneなら master_key と同ディレクトリ。
        today: 初回登録日。Noneなら実行日。

    Returns:
        DirectoryAnonymizationResult
    """
    today = today or date.today().isoformat()
    slot_key_path = slot_key_path or master_key_path.parent / "slot_key.csv"

    csv_files = [f for f in sorted(raw_dir.glob("*.csv")) if f.is_file()]
    if not csv_files:
        raise FileNotFoundError(f"CSVファイルが見つかりません: {raw_dir}")

    logger.info("ディレクトリ匿名化開始: %d ファイル (%s)", len(csv_files), raw_dir)

    frames: list[pd.DataFrame] = []
    for f in csv_files:
        logger.info("  読込: %s", f.name)
        frames.append(_read_csv_auto_encoding(f))

    merged = pd.concat(frames, ignore_index=True)
    logger.info("マージ完了: %d 行", len(merged))

    df, newly_registered = _anonymize_df(
        merged, master_key_path, dept_classification_path, slot_key_path, today
    )

    dates = pd.to_datetime(df[DATE_COLUMN], errors="coerce")
    df["_month"] = dates.dt.to_period("M").astype(str)

    output_dir.mkdir(parents=True, exist_ok=True)
    months: list[str] = []

    for month, group in df.groupby("_month"):
        month = str(month)
        if month == "NaT":
            logger.warning("日付不正な %d 行をスキップ", len(group))
            continue
        out_path = output_dir / f"raw_data_{month}.csv"
        group.drop(columns=["_month"]).to_csv(out_path, index=False, encoding="utf-8-sig")
        months.append(month)
        logger.info("月別出力: %s → %s (%d行)", month, out_path.name, len(group))

    logger.info(
        "ディレクトリ匿名化完了: %d行 / %d月分 / 新規医師 %d名",
        len(df), len(months), len(newly_registered),
    )
    return DirectoryAnonymizationResult(
        input_files=csv_files,
        months=sorted(months),
        total_rows=len(df),
        newly_registered=newly_registered,
    )
