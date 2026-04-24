"""曜日×時間帯ヒートマップ ダッシュボード生成。

`12_hourly_load.csv` を用いて、看護師配置最適化のための
 - 到着件数（日平均）
 - 同時並行診察数（中央値 / 最大）
を、診療科フィルタ（全体/内科系/外科系/個別科）付きの
曜日×30分ヒートマップとして1枚のHTMLで可視化する。
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
)
from src.core.classify import DeptClassifier

logger = logging.getLogger(__name__)

_WEEKDAYS = ["月", "火", "水", "木", "金", "土"]
_NURSE_CUTOFF_H = 15
_PEAK_WARN_HOURS = (9, 10)


def _bin_labels() -> list[str]:
    labels = []
    for i in range(HEATMAP_BIN_COUNT):
        total = HEATMAP_DAY_START_H * 60 + i * HEATMAP_BIN_MIN
        labels.append(f"{total // 60:02d}:{total % 60:02d}")
    return labels


def _load_hourly(aggregated_root: Path, month: str) -> pd.DataFrame:
    p = aggregated_root / month / "12_hourly_load.csv"
    if not p.exists():
        raise FileNotFoundError(f"12_hourly_load.csv が存在しません: {p}")
    return pd.read_csv(p, encoding="utf-8-sig")


def _build_matrix(
    df: pd.DataFrame, metric: str
) -> list[list[float]]:
    """曜日(0-5) × bin(0..N-1) の2D配列。欠損は0埋め。"""
    matrix = [[0.0] * HEATMAP_BIN_COUNT for _ in range(len(_WEEKDAYS))]
    for _, r in df.iterrows():
        wd = int(r["曜日"])
        bin_idx = int(r["bin_idx"])
        if 0 <= wd < len(_WEEKDAYS) and 0 <= bin_idx < HEATMAP_BIN_COUNT:
            matrix[wd][bin_idx] = float(r[metric])
    return matrix


def _build_dataset(
    hourly_df: pd.DataFrame,
    classifier: DeptClassifier,
) -> dict[str, Any]:
    """フィルタ切替用の複数系列を1つのJSONに束ねる。

    フィルタキー:
        "all"       : 全体（診療科横断で合計）
        "naika"     : 内科系
        "geka"      : 外科系
        個別科コード : "DEPT_<code>"
    各系列は { arrivals, concurrent_median, concurrent_max } の3行列を持つ。
    """
    df = hourly_df.copy()
    df["type"] = df["診療科名"].map(lambda n: classifier.get_type(n))
    df["code"] = df["診療科名"].map(lambda n: classifier.get_code(n))

    series: dict[str, Any] = {}

    def _sum_matrix(sub: pd.DataFrame, label: str) -> dict[str, Any]:
        agg = (
            sub.groupby(["曜日", "bin_idx"])
            .agg(
                到着件数_日平均=("到着件数_日平均", "sum"),
                同時並行_中央値=("同時並行_中央値", "sum"),
                同時並行_最大=("同時並行_最大", "sum"),
            )
            .reset_index()
        )
        return {
            "label": label,
            "arrivals": _build_matrix(agg, "到着件数_日平均"),
            "concurrent_median": _build_matrix(agg, "同時並行_中央値"),
            "concurrent_max": _build_matrix(agg, "同時並行_最大"),
        }

    series["all"] = _sum_matrix(df, "全体")
    series["naika"] = _sum_matrix(df[df["type"] == "内科系"], "内科系")
    series["geka"] = _sum_matrix(df[df["type"] == "外科系"], "外科系")

    for info in classifier.evaluation_targets():
        sub = df[df["診療科名"] == info.name]
        if sub.empty:
            continue
        key = f"DEPT_{info.code}"
        agg = (
            sub.groupby(["曜日", "bin_idx"])
            .agg(
                到着件数_日平均=("到着件数_日平均", "sum"),
                同時並行_中央値=("同時並行_中央値", "sum"),
                同時並行_最大=("同時並行_最大", "sum"),
            )
            .reset_index()
        )
        series[key] = {
            "label": info.name,
            "arrivals": _build_matrix(agg, "到着件数_日平均"),
            "concurrent_median": _build_matrix(agg, "同時並行_中央値"),
            "concurrent_max": _build_matrix(agg, "同時並行_最大"),
        }
    return series


def _build_filter_options(
    classifier: DeptClassifier, dataset: dict[str, Any]
) -> list[dict[str, str]]:
    opts: list[dict[str, str]] = [
        {"key": "all", "label": "全体"},
        {"key": "naika", "label": "内科系"},
        {"key": "geka", "label": "外科系"},
    ]
    for info in classifier.evaluation_targets():
        key = f"DEPT_{info.code}"
        if key in dataset:
            opts.append({"key": key, "label": f"{info.name}（{info.type}）"})
    return opts


def _build_bin_meta() -> list[dict[str, Any]]:
    """各ビンのメタ情報（ラベル、15時以降フラグ、9-10時警告フラグ）。"""
    meta = []
    for i, label in enumerate(_bin_labels()):
        hour = int(label.split(":")[0])
        meta.append({
            "idx": i,
            "label": label,
            "after_cutoff": hour >= _NURSE_CUTOFF_H,
            "morning_peak": hour in _PEAK_WARN_HOURS,
        })
    return meta


def build_hourly_heatmap(
    months: list[str],
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    theme_css: str,
    common_js: str,
    default_month: str | None = None,
) -> Path:
    """曜日×時間帯ヒートマップHTMLを1枚生成する（全月埋め込み）。"""
    classifier = DeptClassifier(classification_path)
    if not months:
        raise ValueError("months が空です")

    sorted_months = sorted(months)
    default_month = default_month or sorted_months[-1]

    dataset_by_month: dict[str, Any] = {}
    filter_option_union: dict[str, dict[str, str]] = {}
    for m in sorted_months:
        hourly = _load_hourly(aggregated_root, m)
        ds = _build_dataset(hourly, classifier)
        dataset_by_month[m] = ds
        for opt in _build_filter_options(classifier, ds):
            filter_option_union.setdefault(opt["key"], opt)

    filter_options = list(filter_option_union.values())
    bin_meta = _build_bin_meta()

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    body = env.get_template("hourly_heatmap.html").render(
        months=sorted_months,
        default_month=default_month,
        weekdays=_WEEKDAYS,
        bin_meta=bin_meta,
        filter_options=filter_options,
        dataset_json=json.dumps(dataset_by_month, ensure_ascii=False),
        nurse_cutoff_h=_NURSE_CUTOFF_H,
        peak_warn_hours=list(_PEAK_WARN_HOURS),
        common_js=common_js,
    )
    html = env.get_template("base.html").render(
        title=f"曜日×時間帯ヒートマップ {default_month}",
        site_title=f"曜日×時間帯ヒートマップ ({sorted_months[0]} 〜 {sorted_months[-1]})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("曜日×時間帯ヒートマップ出力: %s (%d ヶ月)", output_path, len(sorted_months))
    return output_path
