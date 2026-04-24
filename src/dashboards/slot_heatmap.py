"""外来枠(予約名称)×時間帯ヒートマップ ダッシュボード生成。

`15_slot_hourly.csv` を用いて、診療科フィルタ内の予約名称ごとに
曜日×30分ビンの稼働パターンを可視化する。医師ヒートマップの「枠視点」版。
- 稼働頻度率: そのビンで1件以上診察した日数 / その曜日が月内に存在した日数
- 件数合計: そのビンでの月内総診察件数
- 実診察分数: そのビンと各診察の時間重なり分（分）の月内合計

Y軸=予約名称、X軸=30分ビン、曜日はタブ切替。枠再編(縮小・統合・廃止)
のための情報提供を主目的とするため、低稼働枠も非表示にせず、
デフォルトでは低稼働順(件数昇順)に並べて縮小候補を上位に表示する。
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
    HEATMAP_BIN_COUNT,
    HEATMAP_BIN_MIN,
    HEATMAP_DAY_START_H,
    SLOT_HOURLY_CATEGORIES,
    SLOT_ID_COLUMN,
)
from src.core.classify import DeptClassifier

logger = logging.getLogger(__name__)

_WEEKDAYS = ["月", "火", "水", "木", "金", "土"]
_METRIC_KEYS = ("frequency", "count", "duration", "count_per_day", "duration_per_day")
_METRIC_COLUMNS = {
    "frequency": "稼働頻度率",
    "count": "件数合計",
    "duration": "実診察分数",
    "count_per_day": "件数_日平均",
    "duration_per_day": "実診察分数_日平均",
}
_TOTAL_CELLS = len(_WEEKDAYS) * HEATMAP_BIN_COUNT


def _bin_labels() -> list[str]:
    labels = []
    for i in range(HEATMAP_BIN_COUNT):
        total = HEATMAP_DAY_START_H * 60 + i * HEATMAP_BIN_MIN
        labels.append(f"{total // 60:02d}:{total % 60:02d}")
    return labels


def _load_slot_hourly(aggregated_root: Path, month: str) -> pd.DataFrame:
    p = aggregated_root / month / "15_slot_hourly.csv"
    if not p.exists():
        raise FileNotFoundError(f"15_slot_hourly.csv が存在しません: {p}")
    return pd.read_csv(p, encoding="utf-8-sig")


def _empty_matrix() -> list[list[float]]:
    return [[0.0] * HEATMAP_BIN_COUNT for _ in range(len(_WEEKDAYS))]


def _build_dept_series(sub: pd.DataFrame) -> list[dict[str, Any]]:
    """診療科内の予約名称ごとに 区分×weekday×bin の指標行列を組み立てる。

    デフォルト並び順は全体区分の月内総件数の昇順(低稼働枠=縮小候補が先頭)。
    クライアント側で降順/名称順への切替が可能。
    """
    if sub.empty:
        return []

    if "区分" not in sub.columns:
        sub = sub.assign(区分="全体")

    zentai = sub[sub["区分"] == "全体"]
    totals = (
        zentai.groupby(SLOT_ID_COLUMN)["件数合計"].sum().sort_values(ascending=True)
    )
    slot_names = totals.index.tolist()

    rows: list[dict[str, Any]] = []
    for sid in slot_names:
        ssub = sub[sub[SLOT_ID_COLUMN] == sid]
        categories: dict[str, dict[str, list[list[float]]]] = {}
        active_cells = 0
        for cat in SLOT_HOURLY_CATEGORIES:
            cat_sub = ssub[ssub["区分"] == cat]
            matrices = {mk: _empty_matrix() for mk in _METRIC_KEYS}
            for _, r in cat_sub.iterrows():
                wd = int(r["曜日"])
                bi = int(r["bin_idx"])
                if not (0 <= wd < len(_WEEKDAYS)) or not (0 <= bi < HEATMAP_BIN_COUNT):
                    continue
                for mk, col in _METRIC_COLUMNS.items():
                    matrices[mk][wd][bi] = float(r[col])
            if cat == "全体":
                for wd_row in matrices["count"]:
                    for v in wd_row:
                        if v > 0:
                            active_cells += 1
            categories[cat] = matrices
        rows.append({
            "id": str(sid),
            "total": int(totals.loc[sid]),
            "active_cells": active_cells,
            "total_cells": _TOTAL_CELLS,
            "categories": categories,
        })
    return rows


def _build_dataset(
    hourly_df: pd.DataFrame,
    classifier: DeptClassifier,
) -> dict[str, Any]:
    """診療科コードをキーとする枠別データセット。"""
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
            "slots": _build_dept_series(sub),
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


def build_slot_heatmap(
    months: list[str],
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    theme_css: str,
    common_js: str,
    default_month: str | None = None,
) -> Path:
    """外来枠×時間帯ヒートマップHTMLを1枚生成する（全月埋め込み）。"""
    classifier = DeptClassifier(classification_path)
    if not months:
        raise ValueError("months が空です")

    sorted_months = sorted(months)
    default_month = default_month or sorted_months[-1]

    dataset_by_month: dict[str, Any] = {}
    filter_option_union: dict[str, dict[str, str]] = {}
    for m in sorted_months:
        hourly = _load_slot_hourly(aggregated_root, m)
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
    body = env.get_template("slot_heatmap.html").render(
        months=sorted_months,
        default_month=default_month,
        weekdays=_WEEKDAYS,
        bin_labels=bin_labels,
        filter_options=filter_options,
        dataset_json=json.dumps(dataset_by_month, ensure_ascii=False),
        common_js=common_js,
    )
    html = env.get_template("base.html").render(
        title=f"外来枠×時間帯ヒートマップ {default_month}",
        site_title=f"外来枠×時間帯ヒートマップ ({sorted_months[0]} 〜 {sorted_months[-1]})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info(
        "外来枠×時間帯ヒートマップ出力: %s (%d ヶ月)", output_path, len(sorted_months)
    )
    return output_path
