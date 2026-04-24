"""診療科別 深掘りダッシュボード生成。

集計CSV（`data/aggregated/YYYY-MM/`）から指定月・指定診療科の
時間帯分布／医師別内訳／逆紹介候補を抽出してHTML化する。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.classify import DeptClassifier
from src.core.data_loader import load_aggregated_data
from src.core.grading import achievement_pct, grade_from_achievement

logger = logging.getLogger(__name__)

DOCTOR_LIMIT = 10
TIMEZONE_ORDER = [
    "午前(〜12時)",
    "午後前半(12-15時)",
    "午後後半(15-17時)",
    "夕方以降(17時〜)",
]
WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]


@dataclass
class _DeptSummary:
    total: int
    sho: int
    sai: int
    shokai_sho: int
    mirain: int
    sho_rate: float
    shokai_rate: float
    mirain_rate: float


def _summary_for_dept(kpi_df: pd.DataFrame, dept: str, month: str) -> _DeptSummary | None:
    row = kpi_df[(kpi_df["診療科名"] == dept) & (kpi_df["月"].astype(str) == month)]
    if row.empty:
        return None
    r = row.iloc[0]
    return _DeptSummary(
        total=int(r["総件数"]),
        sho=int(r["初診件数"]),
        sai=int(r["再診件数"]),
        shokai_sho=int(r["紹介状あり初診"]),
        mirain=int(r["未来院件数"]),
        sho_rate=float(r["初診率"]),
        shokai_rate=float(r["紹介率"]),
        mirain_rate=float(r["未来院率"]),
    )


def _kpi_evaluations(
    summary: _DeptSummary, target: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sho_target = int(target.get("初診目標_月", 0) or 0)
    if sho_target > 0:
        pct = achievement_pct(summary.sho, sho_target)
        rows.append({
            "label": "初診件数",
            "actual": summary.sho,
            "target": sho_target,
            "grade_badge": _badge(grade_from_achievement(pct)),
        })
    sps_target = float(target.get("再診初診比率_目標", 0) or 0)
    if sps_target > 0 and summary.sho > 0:
        sps = round(summary.sai / summary.sho, 1)
        pct = achievement_pct(sps, sps_target)
        rows.append({
            "label": "再診/初診 比率",
            "actual": sps,
            "target": sps_target,
            "grade_badge": _badge(grade_from_achievement(pct, inverse=True)),
        })
    return rows


def _badge(grade: str) -> str:
    return f'<span class="grade grade-{grade}">{grade}</span>'


def _timezone_chart_data(tz_df: pd.DataFrame, dept: str) -> dict[str, Any]:
    sub = tz_df[tz_df["診療科名"] == dept]
    if sub.empty:
        return {"labels": [], "datasets": []}

    datasets: list[dict[str, Any]] = []
    for zone in TIMEZONE_ORDER:
        zone_df = sub[sub["時間帯ゾーン"] == zone]
        counts = []
        for wd in range(7):
            count = int(zone_df[zone_df["曜日"] == wd]["件数"].sum())
            counts.append(count)
        datasets.append({"label": zone, "data": counts})

    return {"labels": WEEKDAY_LABELS, "datasets": datasets}


def _top_doctors(
    doctor_df: pd.DataFrame, dept: str, use_real_names: bool = False
) -> list[dict[str, Any]]:
    sub = doctor_df[doctor_df["診療科名"] == dept]
    if sub.empty:
        return []

    agg = (
        sub.groupby("予約担当者匿名ID")
        .apply(lambda g: pd.Series({
            "total": int(g["件数"].sum()),
            "sho": int(g[g["初再診区分"] == "初診"]["件数"].sum()),
            "sai": int(g[g["初再診区分"] == "再診"]["件数"].sum()),
            "shokai_sho": int(g[
                (g["初再診区分"] == "初診") & (g["紹介状有無"] == "紹介状あり")
            ]["件数"].sum()),
        }))
        .sort_values("total", ascending=False)
        .head(DOCTOR_LIMIT)
        .reset_index()
    )

    doctor_ids = agg["予約担当者匿名ID"].tolist()
    rows = []
    for i, row in enumerate(agg.itertuples(index=False), start=1):
        if use_real_names:
            display = str(doctor_ids[i - 1])
        else:
            display = f"医師{chr(64 + i) if i <= 26 else i}"
        rows.append({
            "display_name": display,
            "total": int(row.total),
            "sho": int(row.sho),
            "sai": int(row.sai),
            "shokai_sho": int(row.shokai_sho),
        })
    return rows


def _reverse_referral(
    rr_df: pd.DataFrame, dept: str, month: str
) -> tuple[list[dict[str, Any]], int]:
    sub = rr_df[
        (rr_df["診療科名"] == dept)
        & (rr_df["月"].astype(str) == month)
        & (rr_df["初再診区分"] == "再診")
        & (rr_df["紹介状有無"] == "紹介状無し")
        & (rr_df["併科受診フラグ"] == "無")
        & (rr_df["診察前検査フラグ"] == "なし")
        & (rr_df["診察時間_階級"].isin(["0-4分", "5-9分"]))
    ]
    grouped = (
        sub.groupby("診察時間_階級")["件数"].sum().reset_index()
        .sort_values("診察時間_階級")
    )
    rows = [
        {"bucket": str(r["診察時間_階級"]), "count": int(r["件数"])}
        for _, r in grouped.iterrows()
    ]
    total = int(grouped["件数"].sum()) if not grouped.empty else 0
    return rows, total


def _render(
    env: Environment,
    *,
    dept_name: str,
    dept_type: str,
    month: str,
    summary: _DeptSummary,
    kpis: list[dict[str, Any]],
    timezone_data: dict[str, Any],
    doctors: list[dict[str, Any]],
    reverse_referral_rows: list[dict[str, Any]],
    reverse_referral_total: int,
    theme_css: str,
    common_js: str,
) -> str:
    type_key = {"外科系": "geka", "内科系": "naika"}.get(dept_type, "other")
    type_label = {"geka": "外科", "naika": "内科", "other": "その他"}[type_key]
    type_badge = f'<span class="type-badge type-{type_key}">{type_label}</span>'

    body = env.get_template("dept_drilldown.html").render(
        dept_name=dept_name,
        type_badge=type_badge,
        month=month,
        summary={
            "total": f"{summary.total:,}",
            "sho": f"{summary.sho:,}",
            "sai": f"{summary.sai:,}",
            "shokai_sho": f"{summary.shokai_sho:,}",
            "mirain": f"{summary.mirain:,}",
            "sho_rate": summary.sho_rate,
            "shokai_rate": summary.shokai_rate,
            "mirain_rate": summary.mirain_rate,
        },
        kpis=kpis,
        doctor_limit=DOCTOR_LIMIT,
        doctors=doctors,
        reverse_referral=reverse_referral_rows,
        reverse_referral_total=f"{reverse_referral_total:,}",
        timezone_data_json=json.dumps(timezone_data, ensure_ascii=False),
        common_js=common_js,
    )
    return env.get_template("base.html").render(
        title=f"{dept_name} 深掘り {month}",
        site_title=f"{dept_name} 深掘り ({month})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )


def build_dept_drilldown(
    month: str,
    aggregated_root: Path,
    templates_dir: Path,
    output_dir: Path,
    classification_path: Path,
    targets_path: Path,
    theme_css: str,
    common_js: str,
    use_real_names: bool = False,
) -> list[Path]:
    """評価対象の全診療科について深掘りHTMLを一括生成する。"""
    classifier = DeptClassifier(classification_path)
    data = load_aggregated_data(aggregated_root, month)
    targets_df = (
        pd.read_csv(targets_path, encoding="utf-8-sig")
        if targets_path.exists() else pd.DataFrame()
    )
    target_map = (
        {str(r["診療科名"]): r.to_dict() for _, r in targets_df.iterrows()}
        if not targets_df.empty else {}
    )

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    for info in classifier.evaluation_targets():
        summary = _summary_for_dept(data.referral_kpi, info.name, month)
        if summary is None or summary.total == 0:
            logger.info("データなしのためスキップ: %s", info.name)
            continue

        kpis = _kpi_evaluations(summary, target_map.get(info.name, {}))
        timezone_data = _timezone_chart_data(data.dept_timezone, info.name)
        doctors = _top_doctors(data.doctor_summary, info.name, use_real_names=use_real_names)
        rr_rows, rr_total = _reverse_referral(data.reverse_referral, info.name, month)

        html = _render(
            env,
            dept_name=info.name,
            dept_type=info.type,
            month=month,
            summary=summary,
            kpis=kpis,
            timezone_data=timezone_data,
            doctors=doctors,
            reverse_referral_rows=rr_rows,
            reverse_referral_total=rr_total,
            theme_css=theme_css,
            common_js=common_js,
        )

        out_path = output_dir / f"{info.code}.html"
        out_path.write_text(html, encoding="utf-8")
        generated.append(out_path)
        logger.info("深掘りHTML出力: %s", out_path)

    return generated
