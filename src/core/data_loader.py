"""集計CSV群の読込と構造化。

data/aggregated/YYYY-MM/ 配下の12ファイルをまとめて読み込み、
ダッシュボード生成層が扱いやすい AggregatedData にして返す。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class AggregatedData:
    """集計CSV群の読込結果。"""

    month: str
    summary: pd.DataFrame
    dept_weekday_hour: pd.DataFrame
    dept_monthly: pd.DataFrame
    dept_time_stats: pd.DataFrame
    doctor_summary: pd.DataFrame
    dept_reception: pd.DataFrame
    room_30min: pd.DataFrame
    slot_analysis: pd.DataFrame
    reverse_referral: pd.DataFrame
    concurrent_pairs: pd.DataFrame
    referral_kpi: pd.DataFrame
    dept_timezone: pd.DataFrame


_FILES = {
    "summary": "00_summary.csv",
    "dept_weekday_hour": "01_dept_weekday_hour.csv",
    "dept_monthly": "02_dept_monthly.csv",
    "dept_time_stats": "03_dept_time_stats.csv",
    "doctor_summary": "04_doctor_summary.csv",
    "dept_reception": "05_dept_reception.csv",
    "room_30min": "06_room_30min.csv",
    "slot_analysis": "07_slot_analysis.csv",
    "reverse_referral": "08_reverse_referral.csv",
    "concurrent_pairs": "09_concurrent_pairs.csv",
    "referral_kpi": "10_referral_kpi.csv",
    "dept_timezone": "11_dept_timezone.csv",
}


def load_aggregated_data(aggregated_root: Path, month: str) -> AggregatedData:
    """指定月の集計CSV群を読み込む。

    Args:
        aggregated_root: data/aggregated/ のパス
        month: "YYYY-MM" 形式

    Returns:
        AggregatedData
    """
    base = aggregated_root / month
    if not base.exists():
        raise FileNotFoundError(f"集計ディレクトリが存在しません: {base}")

    frames = {
        key: pd.read_csv(base / fname, encoding="utf-8-sig")
        for key, fname in _FILES.items()
    }
    return AggregatedData(month=month, **frames)


def load_multi_month(
    aggregated_root: Path, months: list[str]
) -> dict[str, AggregatedData]:
    """複数月をまとめて読み込む。"""
    return {m: load_aggregated_data(aggregated_root, m) for m in months}


def load_last_n_months(
    aggregated_root: Path, current_month: str, n: int = 6
) -> list[str]:
    """current_month を含む直近 n ヶ月の月リストを返す（存在するディレクトリのみ）。

    Args:
        aggregated_root: data/aggregated/ のパス
        current_month: 末尾月（"YYYY-MM"）
        n: 取得上限月数

    Returns:
        昇順の月リスト。n に満たない場合は警告ログを出して存在分のみ返す。
    """
    import logging
    import re

    logger = logging.getLogger(__name__)
    pat = re.compile(r"^\d{4}-\d{2}$")

    available = sorted(
        d.name
        for d in aggregated_root.iterdir()
        if d.is_dir() and pat.match(d.name) and d.name <= current_month
    )

    months = available[-n:]

    if len(months) < n:
        logger.warning(
            "直近 %d ヶ月分のデータが揃っていません（%d ヶ月分のみ）: %s",
            n, len(months), months,
        )

    return months
