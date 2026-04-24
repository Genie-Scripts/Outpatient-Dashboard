"""医師×時間帯ヒートマップ ダッシュボード生成。

`14_doctor_hourly.csv` を用いて、診療科フィルタ内の医師ごとに
曜日×30分ビンの出勤パターンを可視化する。
- 出勤頻度率: そのビンで診察した日数 / その曜日が月内に存在した日数
- 件数合計: そのビンでの月内総診察件数
- 実診察分数: そのビンと各診察の時間重なり分（分）の月内合計

Y軸=医師匿名ID（DR_U001 等、マスターで追跡可能なまま）、X軸=30分ビン、
曜日はタブ切替。空き枠の可視化と外来枠偏りの把握を目的とする。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.aggregate import (
    DOCTOR_HOURLY_CATEGORIES,
    DOCTOR_ID_COLUMN,
    HEATMAP_BIN_COUNT,
    HEATMAP_BIN_MIN,
    HEATMAP_DAY_START_H,
)
from src.core.classify import DeptClassifier

logger = logging.getLogger(__name__)

_WEEKDAYS = ["月", "火", "水", "木", "金", "土"]
_METRIC_KEYS = ("frequency", "count", "duration", "count_per_day", "duration_per_day")
_METRIC_COLUMNS = {
    "frequency": "出勤頻度率",
    "count": "件数合計",
    "duration": "実診察分数",
    "count_per_day": "件数_日平均",
    "duration_per_day": "実診察分数_日平均",
}


def _bin_labels() -> list[str]:
    labels = []
    for i in range(HEATMAP_BIN_COUNT):
        total = HEATMAP_DAY_START_H * 60 + i * HEATMAP_BIN_MIN
        labels.append(f"{total // 60:02d}:{total % 60:02d}")
    return labels


def _load_doctor_hourly(aggregated_root: Path, month: str) -> pd.DataFrame:
    p = aggregated_root / month / "14_doctor_hourly.csv"
    if not p.exists():
        raise FileNotFoundError(f"14_doctor_hourly.csv が存在しません: {p}")
    return pd.read_csv(p, encoding="utf-8-sig")


def _empty_matrix() -> list[list[float]]:
    return [[0.0] * HEATMAP_BIN_COUNT for _ in range(len(_WEEKDAYS))]


def _build_dept_series(sub: pd.DataFrame) -> list[dict[str, Any]]:
    """診療科内の医師ごとに 区分×weekday×bin の指標行列を組み立てる。

    医師は「全体」区分の月内総件数の多い順に並べる。
    旧CSV（区分列なし）の場合は「全体」扱いで処理する。
    """
    if sub.empty:
        return []

    if "区分" not in sub.columns:
        sub = sub.assign(区分="全体")
    if "件数_日平均" not in sub.columns:
        days = sub["該当日数"].replace(0, pd.NA)
        sub = sub.assign(
            件数_日平均=(sub["件数合計"] / days).fillna(0).round(2),
            実診察分数_日平均=(sub["実診察分数"] / days).fillna(0).round(1),
        )

    zentai = sub[sub["区分"] == "全体"]
    totals = (
        zentai.groupby(DOCTOR_ID_COLUMN)["件数合計"].sum().sort_values(ascending=False)
    )
    doctor_ids = totals.index.tolist()

    rows: list[dict[str, Any]] = []
    for did in doctor_ids:
        dsub = sub[sub[DOCTOR_ID_COLUMN] == did]
        categories: dict[str, dict[str, list[list[float]]]] = {}
        for cat in DOCTOR_HOURLY_CATEGORIES:
            cat_sub = dsub[dsub["区分"] == cat]
            matrices = {mk: _empty_matrix() for mk in _METRIC_KEYS}
            for _, r in cat_sub.iterrows():
                wd = int(r["曜日"])
                bi = int(r["bin_idx"])
                if not (0 <= wd < len(_WEEKDAYS)) or not (0 <= bi < HEATMAP_BIN_COUNT):
                    continue
                for mk, col in _METRIC_COLUMNS.items():
                    matrices[mk][wd][bi] = float(r[col])
            categories[cat] = matrices
        rows.append({
            "id": str(did),
            "total": int(totals.loc[did]),
            "categories": categories,
        })
    return rows


def _build_dataset(
    hourly_df: pd.DataFrame,
    classifier: DeptClassifier,
) -> dict[str, Any]:
    """診療科コードをキーとする医師別データセット。

    `weekday_day_count` は曜日別の月内存在日数（出勤頻度率の分母参考）。
    """
    df = hourly_df.copy()
    weekday_days = (
        df.drop_duplicates(["曜日"])[["曜日", "該当日数"]]
        .set_index("曜日")["該当日数"].to_dict()
    )
    weekday_day_count = [int(weekday_days.get(i, 0)) for i in range(len(_WEEKDAYS))]

    series: dict[str, Any] = {}
    for info in classifier.evaluation_targets():
        sub = df[df["診療科名"] == info.name]
        if sub.empty:
            continue
        key = f"DEPT_{info.code}"
        series[key] = {
            "label": info.name,
            "type": info.type,
            "weekday_day_count": weekday_day_count,
            "doctors": _build_dept_series(sub),
        }
    return series


def _build_filter_options(
    classifier: DeptClassifier, dataset: dict[str, Any]
) -> list[dict[str, str]]:
    opts: list[dict[str, str]] = []
    for info in classifier.evaluation_targets():
        key = f"DEPT_{info.code}"
        if key in dataset:
            opts.append({"key": key, "label": f"{info.name}（{info.type}）"})
    return opts


def build_doctor_heatmap(
    months: list[str],
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    theme_css: str,
    common_js: str,
    default_month: str | None = None,
) -> Path:
    """医師×時間帯ヒートマップHTMLを1枚生成する（全月埋め込み）。"""
    classifier = DeptClassifier(classification_path)
    if not months:
        raise ValueError("months が空です")

    sorted_months = sorted(months)
    default_month = default_month or sorted_months[-1]

    dataset_by_month: dict[str, Any] = {}
    filter_option_union: dict[str, dict[str, str]] = {}
    for m in sorted_months:
        hourly = _load_doctor_hourly(aggregated_root, m)
        ds = _build_dataset(hourly, classifier)
        dataset_by_month[m] = ds
        for opt in _build_filter_options(classifier, ds):
            filter_option_union.setdefault(opt["key"], opt)

    filter_options = list(filter_option_union.values())
    bin_labels = _bin_labels()

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    body = env.get_template("doctor_heatmap.html").render(
        months=sorted_months,
        default_month=default_month,
        weekdays=_WEEKDAYS,
        bin_labels=bin_labels,
        filter_options=filter_options,
        dataset_json=json.dumps(dataset_by_month, ensure_ascii=False),
        common_js=common_js,
    )
    html = env.get_template("base.html").render(
        title=f"医師×時間帯ヒートマップ {default_month}",
        site_title=f"医師×時間帯ヒートマップ ({sorted_months[0]} 〜 {sorted_months[-1]})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info(
        "医師×時間帯ヒートマップ出力: %s (%d ヶ月)", output_path, len(sorted_months)
    )
    return output_path
