"""外来データ集計モジュール。

匿名化済みCSV（data/raw/anonymized/raw_data_YYYY-MM.csv）を読み、
12種類の集計CSVを data/aggregated/YYYY-MM/ 配下に出力する。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_ANON_FILE_RE = re.compile(r"^raw_data_(\d{4}-\d{2})\.csv$")

logger = logging.getLogger(__name__)

DOCTOR_ID_COLUMN = "予約担当者匿名ID"
SLOT_ID_COLUMN = "予約名称"

HEATMAP_DAY_START_H = 8
HEATMAP_DAY_END_H = 20
HEATMAP_BIN_MIN = 30
HEATMAP_BIN_COUNT = (HEATMAP_DAY_END_H - HEATMAP_DAY_START_H) * 60 // HEATMAP_BIN_MIN

DRUG_REVISIT_SHORT_EXAM_MIN = 4
DRUG_REVISIT_MIN_RECORDS = 10


@dataclass
class AggregationResult:
    """集計実行結果のサマリ。"""

    input_path: Path
    output_dir: Path
    month: str
    total_rows: int
    generated_files: list[str]


def _classify_exam_time(t: float) -> str:
    """診察時間を階級分け。"""
    if pd.isna(t) or t < 0:
        return "不明"
    if t < 5:
        return "0-4分"
    if t < 10:
        return "5-9分"
    if t < 15:
        return "10-14分"
    if t < 30:
        return "15-29分"
    return "30分以上"


def _classify_wait_time(t: float) -> str:
    """診察待時間を階級分け。"""
    if pd.isna(t) or t < 0:
        return "不明"
    if t < 30:
        return "0-29分"
    if t < 60:
        return "30-59分"
    if t < 90:
        return "60-89分"
    if t < 120:
        return "90-119分"
    return "120分以上"


def _time_zone(h: float) -> str:
    """受付時刻（時）を時間帯ゾーンに分類。"""
    if pd.isna(h):
        return "不明"
    if h < 12:
        return "午前(〜12時)"
    if h < 15:
        return "午後前半(12-15時)"
    if h < 17:
        return "午後後半(15-17時)"
    return "夕方以降(17時〜)"


def _read_csv_auto_encoding(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp932")


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """日付・時刻の前処理と階級分け。"""
    df = df.copy()
    df["予約日"] = pd.to_datetime(df["予約日"], errors="coerce")
    df["曜日"] = df["予約日"].dt.dayofweek
    df["月"] = df["予約日"].dt.to_period("M").astype(str)

    uketsuke = pd.to_datetime(df["受付時刻"], format="%H:%M:%S", errors="coerce")
    df["受付h"] = uketsuke.dt.hour
    df["受付_30min"] = uketsuke.dt.floor("30min").dt.strftime("%H:%M")

    start = pd.to_datetime(df.get("開始時刻"), format="%H:%M:%S", errors="coerce")
    end = pd.to_datetime(df.get("終了時刻"), format="%H:%M:%S", errors="coerce")
    df["開始_分"] = start.dt.hour * 60 + start.dt.minute
    df["終了_分"] = end.dt.hour * 60 + end.dt.minute

    df["診察時間_階級"] = df["診察時間"].apply(_classify_exam_time)
    df["診察待時間_階級"] = df["診察待時間"].apply(_classify_wait_time)
    df["時間帯ゾーン"] = df["受付h"].apply(_time_zone)
    return df


def _write(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _agg_summary(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([{
        "総件数": len(df),
        "期間_開始": df["予約日"].min().strftime("%Y-%m-%d") if df["予約日"].notna().any() else "",
        "期間_終了": df["予約日"].max().strftime("%Y-%m-%d") if df["予約日"].notna().any() else "",
        "診療科数": df["診療科名"].nunique(),
        "医師数": df[DOCTOR_ID_COLUMN].nunique(),
        "部屋数": df["部屋番号"].nunique(),
        "予約名称_種類数": df["予約名称"].nunique(),
        "初診件数": (df["初再診区分"] == "初診").sum(),
        "再診件数": (df["初再診区分"] == "再診").sum(),
        "紹介状あり": (df["紹介状有無"] == "紹介状あり").sum(),
        "併科受診_有": (df["併科受診フラグ"] == "有").sum(),
        "未来院件数": (df["診療受付区分"] == "未来院").sum(),
    }])


def _agg_time_stats(df: pd.DataFrame) -> pd.DataFrame:
    def stats(x: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "件数": len(x),
            "待_中央値": x["診察待時間"].median(),
            "待_平均": x["診察待時間"].mean(),
            "待_Q1": x["診察待時間"].quantile(0.25),
            "待_Q3": x["診察待時間"].quantile(0.75),
            "診察_中央値": x["診察時間"].median(),
            "診察_平均": x["診察時間"].mean(),
            "診察_Q3": x["診察時間"].quantile(0.75),
            "会計_中央値": x["会計待時間"].median(),
            "会計_平均": x["会計待時間"].mean(),
        })

    return (
        df.groupby(["診療科名", "曜日", "受付h"]).apply(stats).reset_index().round(1)
    )


def _agg_referral_kpi(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (dept, month), g in df.groupby(["診療科名", "月"]):
        total = len(g)
        sho = (g["初再診区分"] == "初診").sum()
        sai = (g["初再診区分"] == "再診").sum()
        shokai_sho = ((g["初再診区分"] == "初診") & (g["紹介状有無"] == "紹介状あり")).sum()
        shokai_all = (g["紹介状有無"] == "紹介状あり").sum()
        mirain = (g["診療受付区分"] == "未来院").sum()
        rows.append({
            "診療科名": dept,
            "月": month,
            "総件数": total,
            "初診件数": int(sho),
            "再診件数": int(sai),
            "紹介状あり初診": int(shokai_sho),
            "紹介状あり全件": int(shokai_all),
            "未来院件数": int(mirain),
            "初診率": round(sho / total * 100, 2) if total else 0,
            "紹介率": round(shokai_sho / sho * 100, 2) if sho else 0,
            "紹介状率_全体": round(shokai_all / total * 100, 2) if total else 0,
            "未来院率": round(mirain / total * 100, 2) if total else 0,
        })
    return pd.DataFrame(rows)


def _valid_time_mask(df: pd.DataFrame) -> pd.Series:
    """開始・終了時刻の異常値（0:00:01、欠損、逆転など）を弾くマスク。"""
    day_start = HEATMAP_DAY_START_H * 60
    day_end = HEATMAP_DAY_END_H * 60
    return (
        df["開始_分"].notna()
        & df["終了_分"].notna()
        & (df["開始_分"] >= day_start)
        & (df["開始_分"] < day_end)
        & (df["終了_分"] > df["開始_分"])
        & (df["診察時間"].between(0.5, 180))
    )


def _agg_hourly_load(df: pd.DataFrame) -> pd.DataFrame:
    """曜日×30分刻みの到着件数と同時並行診察数を集計する。

    - 到着件数_日平均: 当該(曜日, 30分ビン)における1日あたりの平均到着件数
    - 同時並行_中央値: 各日の当該ビンでの重なり件数 → 日間の中央値
    - 同時並行_最大: 日間の最大値（ピーク負荷）
    ビンは 08:00-20:00（12時間 / 30分 = 24ビン）。
    """
    valid = df[_valid_time_mask(df)].copy()
    if valid.empty:
        return pd.DataFrame(columns=[
            "診療科名", "曜日", "bin_idx", "bin_label",
            "到着件数_日平均", "同時並行_中央値", "同時並行_最大", "該当日数",
        ])

    day_start = HEATMAP_DAY_START_H * 60
    valid["start_bin"] = np.clip(
        (valid["開始_分"] - day_start) // HEATMAP_BIN_MIN, 0, HEATMAP_BIN_COUNT - 1
    ).astype(int)
    valid["end_bin"] = np.clip(
        (valid["終了_分"] - 1 - day_start) // HEATMAP_BIN_MIN, 0, HEATMAP_BIN_COUNT - 1
    ).astype(int)

    valid["bin_list"] = valid.apply(
        lambda r: list(range(r["start_bin"], r["end_bin"] + 1)), axis=1
    )
    exploded = valid.explode("bin_list")
    exploded["bin_idx"] = exploded["bin_list"].astype(int)

    per_day = (
        exploded.groupby(["診療科名", "予約日", "曜日", "bin_idx"])
        .size().reset_index(name="同時並行")
    )
    concurrent = (
        per_day.groupby(["診療科名", "曜日", "bin_idx"])
        .agg(
            同時並行_中央値=("同時並行", "median"),
            同時並行_最大=("同時並行", "max"),
            該当日数=("予約日", "nunique"),
        )
        .reset_index()
    )

    arrivals_per_day = (
        valid.groupby(["診療科名", "予約日", "曜日", "start_bin"])
        .size().reset_index(name="到着件数")
        .rename(columns={"start_bin": "bin_idx"})
    )
    arrivals = (
        arrivals_per_day.groupby(["診療科名", "曜日", "bin_idx"])
        .agg(
            到着件数_日平均=("到着件数", "mean"),
            到着_該当日数=("予約日", "nunique"),
        )
        .reset_index()
    )

    merged = concurrent.merge(
        arrivals[["診療科名", "曜日", "bin_idx", "到着件数_日平均"]],
        on=["診療科名", "曜日", "bin_idx"],
        how="left",
    )
    merged["到着件数_日平均"] = merged["到着件数_日平均"].fillna(0).round(2)
    merged["同時並行_中央値"] = merged["同時並行_中央値"].round(1)
    merged["同時並行_最大"] = merged["同時並行_最大"].astype(int)

    def _label(idx: int) -> str:
        total = day_start + idx * HEATMAP_BIN_MIN
        return f"{total // 60:02d}:{total % 60:02d}"

    merged["bin_label"] = merged["bin_idx"].apply(_label)
    return merged[[
        "診療科名", "曜日", "bin_idx", "bin_label",
        "到着件数_日平均", "同時並行_中央値", "同時並行_最大", "該当日数",
    ]].sort_values(["診療科名", "曜日", "bin_idx"]).reset_index(drop=True)


DOCTOR_HOURLY_CATEGORIES = ("全体", "初診", "再診", "薬再診")


def _agg_doctor_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """曜日×30分刻みの医師別出勤パターンを区分別に集計する。

    区分: 全体 / 初診 / 再診 / 薬再診（再診 ∧ 診察時間 ≤ DRUG_REVISIT_SHORT_EXAM_MIN 分）。
    薬再診は再診の部分集合のため、同じ診察が再診と薬再診の双方に計上される。

    - 出勤日数: 当該(医師, 区分, 曜日, bin)で1件以上診察した日数
    - 該当日数: 当該曜日が月内に存在した日数（区分によらず全データで算出）
    - 出勤頻度率: 出勤日数 / 該当日数（0.0-1.0）
    - 件数合計: 当該区分の月内総診察件数（複数ビンにまたがる診察は各ビンに1カウント）
    - 実診察分数: 当該ビンと各診察の時間重なり分（分）の月内合計
    - 件数_日平均: 件数合計 / 該当日数（1日あたり件数）
    - 実診察分数_日平均: 実診察分数 / 該当日数（1日あたり分数）
    ビンは 08:00-20:00（12時間 / 30分 = 24ビン）。
    """
    columns = [
        "診療科名", DOCTOR_ID_COLUMN, "区分", "曜日", "bin_idx", "bin_label",
        "出勤日数", "該当日数", "出勤頻度率",
        "件数合計", "実診察分数", "件数_日平均", "実診察分数_日平均",
    ]
    valid = df[_valid_time_mask(df)].copy()
    if valid.empty:
        return pd.DataFrame(columns=columns)

    day_start = HEATMAP_DAY_START_H * 60
    valid["start_bin"] = np.clip(
        (valid["開始_分"] - day_start) // HEATMAP_BIN_MIN, 0, HEATMAP_BIN_COUNT - 1
    ).astype(int)
    valid["end_bin"] = np.clip(
        (valid["終了_分"] - 1 - day_start) // HEATMAP_BIN_MIN, 0, HEATMAP_BIN_COUNT - 1
    ).astype(int)

    valid["bin_list"] = valid.apply(
        lambda r: list(range(r["start_bin"], r["end_bin"] + 1)), axis=1
    )
    exploded = valid.explode("bin_list").copy()
    exploded["bin_idx"] = exploded["bin_list"].astype(int)

    bin_start_min = day_start + exploded["bin_idx"] * HEATMAP_BIN_MIN
    bin_end_min = bin_start_min + HEATMAP_BIN_MIN
    exploded["overlap_min"] = (
        np.minimum(exploded["終了_分"], bin_end_min)
        - np.maximum(exploded["開始_分"], bin_start_min)
    ).clip(lower=0)

    is_sho = exploded["初再診区分"] == "初診"
    is_sai = exploded["初再診区分"] == "再診"
    is_drug = is_sai & (exploded["診察時間"] <= DRUG_REVISIT_SHORT_EXAM_MIN)
    category_masks: dict[str, pd.Series] = {
        "全体": pd.Series(True, index=exploded.index),
        "初診": is_sho,
        "再診": is_sai,
        "薬再診": is_drug,
    }

    weekday_days = (
        valid.groupby("曜日")["予約日"].nunique().rename("該当日数").reset_index()
    )

    frames: list[pd.DataFrame] = []
    for cat, mask in category_masks.items():
        sub = exploded[mask]
        if sub.empty:
            continue
        per_bin = (
            sub.groupby(["診療科名", DOCTOR_ID_COLUMN, "曜日", "bin_idx"])
            .agg(
                出勤日数=("予約日", "nunique"),
                件数合計=("予約日", "size"),
                実診察分数=("overlap_min", "sum"),
            )
            .reset_index()
        )
        per_bin["区分"] = cat
        frames.append(per_bin)

    if not frames:
        return pd.DataFrame(columns=columns)

    merged = pd.concat(frames, ignore_index=True).merge(
        weekday_days, on="曜日", how="left"
    )
    merged["該当日数"] = merged["該当日数"].fillna(0).astype(int)
    merged["出勤頻度率"] = np.where(
        merged["該当日数"] > 0,
        (merged["出勤日数"] / merged["該当日数"]).round(3),
        0.0,
    )
    merged["件数_日平均"] = np.where(
        merged["該当日数"] > 0,
        (merged["件数合計"] / merged["該当日数"]).round(2),
        0.0,
    )
    merged["実診察分数_日平均"] = np.where(
        merged["該当日数"] > 0,
        (merged["実診察分数"] / merged["該当日数"]).round(1),
        0.0,
    )
    merged["実診察分数"] = merged["実診察分数"].round(1)

    def _label(idx: int) -> str:
        total = day_start + idx * HEATMAP_BIN_MIN
        return f"{total // 60:02d}:{total % 60:02d}"

    merged["bin_label"] = merged["bin_idx"].apply(_label)
    cat_order = pd.Categorical(
        merged["区分"], categories=list(DOCTOR_HOURLY_CATEGORIES), ordered=True
    )
    merged = merged.assign(_cat_order=cat_order)
    return merged[columns + ["_cat_order"]].sort_values(
        ["診療科名", DOCTOR_ID_COLUMN, "_cat_order", "曜日", "bin_idx"]
    ).drop(columns="_cat_order").reset_index(drop=True)


def _agg_drug_revisit_score(df: pd.DataFrame) -> pd.DataFrame:
    """医師×枠×月ごとに薬再診候補スコアを算出する。

    指標:
        - 短時間再診比率: 再診のうち診察時間 ≤ 4分 の比率
        - 紹介状なし再診比率: 再診のうち紹介状なし の比率
        - 診察時間中央値_再診: 再診の診察時間中央値（短いほど薬再診示唆）
    3指標をグローバルMin-Maxで0-100に正規化し、等配分平均で合成スコア化。
    ノイズ抑制のため 再診件数 < DRUG_REVISIT_MIN_RECORDS の行はスコア無し（NaN）。
    """
    sai = df[df["初再診区分"] == "再診"].copy()
    if sai.empty:
        return pd.DataFrame(columns=[
            "診療科名", "医師匿名ID", "予約名称", "月",
            "再診件数", "短時間再診件数", "短時間再診比率",
            "紹介状なし再診件数", "紹介状なし再診比率",
            "診察時間中央値_再診", "スコア",
        ])

    sai["is_short"] = (sai["診察時間"] <= DRUG_REVISIT_SHORT_EXAM_MIN).astype(int)
    sai["is_no_shokai"] = (sai["紹介状有無"] != "紹介状あり").astype(int)

    group = sai.groupby(
        ["診療科名", DOCTOR_ID_COLUMN, SLOT_ID_COLUMN, "月"], dropna=False
    )
    agg = group.agg(
        再診件数=("診察時間", "size"),
        短時間再診件数=("is_short", "sum"),
        紹介状なし再診件数=("is_no_shokai", "sum"),
        診察時間中央値_再診=("診察時間", "median"),
    ).reset_index().rename(columns={DOCTOR_ID_COLUMN: "医師匿名ID"})

    agg["短時間再診比率"] = (agg["短時間再診件数"] / agg["再診件数"]).round(3)
    agg["紹介状なし再診比率"] = (agg["紹介状なし再診件数"] / agg["再診件数"]).round(3)
    agg["診察時間中央値_再診"] = agg["診察時間中央値_再診"].round(1)

    scoreable = agg["再診件数"] >= DRUG_REVISIT_MIN_RECORDS

    def _minmax(series: pd.Series, reverse: bool = False) -> pd.Series:
        sub = series[scoreable]
        if sub.empty:
            return pd.Series([np.nan] * len(series), index=series.index)
        lo, hi = sub.min(), sub.max()
        if hi == lo:
            normalized = pd.Series([50.0] * len(series), index=series.index)
        else:
            normalized = (series - lo) / (hi - lo) * 100
            normalized = normalized.clip(0, 100)
        if reverse:
            normalized = 100 - normalized
        normalized[~scoreable] = np.nan
        return normalized

    n1 = _minmax(agg["短時間再診比率"])
    n2 = _minmax(agg["紹介状なし再診比率"])
    n3 = _minmax(agg["診察時間中央値_再診"], reverse=True)
    agg["スコア"] = ((n1 + n2 + n3) / 3).round(1)

    return agg[[
        "診療科名", "医師匿名ID", "予約名称", "月",
        "再診件数", "短時間再診件数", "短時間再診比率",
        "紹介状なし再診件数", "紹介状なし再診比率",
        "診察時間中央値_再診", "スコア",
    ]].sort_values(["診療科名", "スコア"], ascending=[True, False], na_position="last").reset_index(drop=True)


def aggregate_monthly_data(
    input_path: Path,
    output_dir: Path,
    month: str,
) -> AggregationResult:
    """匿名化済み月次データを12種の集計CSVに変換する。

    Args:
        input_path: 匿名化済みCSV（data/raw/anonymized/raw_data_YYYY-MM.csv）
        output_dir: 出力ベースディレクトリ（data/aggregated/）
        month: 対象月（"YYYY-MM" 形式）

    Returns:
        AggregationResult: 集計サマリ。
    """
    logger.info("集計開始: %s (月=%s)", input_path, month)
    df = _read_csv_auto_encoding(input_path)
    df = _preprocess(df)

    out = output_dir / month
    out.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []

    _write(_agg_summary(df), out / "00_summary.csv")
    generated.append("00_summary.csv")

    agg01 = (
        df.groupby(["診療科名", "曜日", "受付h", "初再診区分", "紹介状有無"], dropna=False)
        .size().reset_index(name="件数")
    )
    _write(agg01, out / "01_dept_weekday_hour.csv")
    generated.append("01_dept_weekday_hour.csv")

    agg02 = (
        df.groupby(["診療科名", "月", "初再診区分", "紹介状有無"], dropna=False)
        .size().reset_index(name="件数")
    )
    _write(agg02, out / "02_dept_monthly.csv")
    generated.append("02_dept_monthly.csv")

    _write(_agg_time_stats(df), out / "03_dept_time_stats.csv")
    generated.append("03_dept_time_stats.csv")

    agg04 = (
        df.groupby(
            ["診療科名", DOCTOR_ID_COLUMN, "曜日", "初再診区分", "紹介状有無"], dropna=False
        )
        .size().reset_index(name="件数")
    )
    _write(agg04, out / "04_doctor_summary.csv")
    generated.append("04_doctor_summary.csv")

    agg05 = (
        df.groupby(["診療科名", "診療受付区分", "予約フラグ", "曜日"], dropna=False)
        .size().reset_index(name="件数")
    )
    _write(agg05, out / "05_dept_reception.csv")
    generated.append("05_dept_reception.csv")

    agg06 = (
        df[df["受付_30min"].notna()]
        .groupby(
            ["部屋番号", "曜日", "受付_30min", "診療科名", "初再診区分"], dropna=False
        )
        .size().reset_index(name="件数")
    )
    _write(agg06, out / "06_room_30min.csv")
    generated.append("06_room_30min.csv")

    agg07 = (
        df.groupby(
            [
                "診療科名", DOCTOR_ID_COLUMN, "予約名称", "初再診区分",
                "紹介状有無", "月",
            ],
            dropna=False,
        )
        .size().reset_index(name="件数")
    )
    _write(agg07, out / "07_slot_analysis.csv")
    generated.append("07_slot_analysis.csv")

    agg08 = (
        df.groupby(
            [
                "診療科名", "月", "診療区分", "診察時間_階級",
                "併科受診フラグ", "紹介状有無", "初再診区分",
                "予約フラグ", "診察前検査フラグ",
            ],
            dropna=False,
        )
        .size().reset_index(name="件数")
    )
    _write(agg08, out / "08_reverse_referral.csv")
    generated.append("08_reverse_referral.csv")

    df_heika = df[df["併科受診フラグ"] == "有"]
    agg09 = (
        df_heika.groupby(
            ["診療科名", "併科診療科略称名1", "曜日", "初再診区分"], dropna=False
        )
        .size().reset_index(name="件数")
    )
    _write(agg09, out / "09_concurrent_pairs.csv")
    generated.append("09_concurrent_pairs.csv")

    _write(_agg_referral_kpi(df), out / "10_referral_kpi.csv")
    generated.append("10_referral_kpi.csv")

    agg11 = (
        df.groupby(["診療科名", "曜日", "時間帯ゾーン", "初再診区分"], dropna=False)
        .size().reset_index(name="件数")
    )
    _write(agg11, out / "11_dept_timezone.csv")
    generated.append("11_dept_timezone.csv")

    _write(_agg_hourly_load(df), out / "12_hourly_load.csv")
    generated.append("12_hourly_load.csv")

    _write(_agg_drug_revisit_score(df), out / "13_drug_revisit_score.csv")
    generated.append("13_drug_revisit_score.csv")

    _write(_agg_doctor_hourly(df), out / "14_doctor_hourly.csv")
    generated.append("14_doctor_hourly.csv")

    logger.info("集計完了: %d行 → %d ファイル (%s)", len(df), len(generated), out)
    return AggregationResult(
        input_path=input_path,
        output_dir=out,
        month=month,
        total_rows=len(df),
        generated_files=generated,
    )


def aggregate_all_months(
    anon_dir: Path,
    output_dir: Path,
) -> list[AggregationResult]:
    """匿名化済みディレクトリ内の全月CSVを集計する。

    ファイル名パターン: raw_data_YYYY-MM.csv

    Args:
        anon_dir: 匿名化済みCSVのディレクトリ（data/raw/anonymized/）
        output_dir: 集計CSV出力先（data/aggregated/）

    Returns:
        月ごとの AggregationResult リスト（月順）。
    """
    files = sorted(
        f for f in anon_dir.glob("*.csv")
        if _ANON_FILE_RE.match(f.name)
    )
    if not files:
        raise FileNotFoundError(f"匿名化済みCSVが見つかりません: {anon_dir}")

    results: list[AggregationResult] = []
    for f in files:
        m = _ANON_FILE_RE.match(f.name)
        month = m.group(1) if m else f.stem
        results.append(aggregate_monthly_data(f, output_dir, month))

    return results
