"""医師別 深掘りダッシュボード生成。

`04_doctor_summary.csv` を用いて、診療科別・医師別の
 - 総件数 / 初診 / 再診 / 紹介状あり初診
 - 再診/初診比
 - 紹介状あり初診率
を1枚のHTMLに集約する。匿名ID（DR_XXX）はそのまま出さず、
診療科内の序列で 医師A, 医師B, ... という表示名に付け替える。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.classify import DeptClassifier
from src.core.data_loader import load_aggregated_data

logger = logging.getLogger(__name__)

DOCTOR_COLUMN = "予約担当者匿名ID"


def _display_name(index: int) -> str:
    if index <= 26:
        return f"医師{chr(64 + index)}"
    return f"医師{index}"


def _dept_doctor_rows(
    doctor_df: pd.DataFrame, dept: str, use_real_names: bool = False
) -> list[dict[str, Any]]:
    sub = doctor_df[doctor_df["診療科名"] == dept]
    if sub.empty:
        return []

    agg = (
        sub.groupby(DOCTOR_COLUMN)
        .apply(lambda g: pd.Series({
            "total": int(g["件数"].sum()),
            "sho": int(g[g["初再診区分"] == "初診"]["件数"].sum()),
            "sai": int(g[g["初再診区分"] == "再診"]["件数"].sum()),
            "shokai_sho": int(g[
                (g["初再診区分"] == "初診") & (g["紹介状有無"] == "紹介状あり")
            ]["件数"].sum()),
        }))
        .sort_values("total", ascending=False)
        .reset_index()
    )

    doctor_ids = agg[DOCTOR_COLUMN].tolist()
    rows: list[dict[str, Any]] = []
    for i, r in enumerate(agg.itertuples(index=False), start=1):
        sps = round(r.sai / r.sho, 1) if r.sho > 0 else 0.0
        shokai_rate = round(r.shokai_sho / r.sho * 100, 1) if r.sho > 0 else 0.0
        rows.append({
            "display_name": str(doctor_ids[i - 1]) if use_real_names else _display_name(i),
            "total": int(r.total),
            "sho": int(r.sho),
            "sai": int(r.sai),
            "shokai_sho": int(r.shokai_sho),
            "sps": sps,
            "shokai_rate": shokai_rate,
        })
    return rows


def _type_label(type_key: str) -> str:
    return {"外科系": "外科", "内科系": "内科"}.get(type_key, "その他")


def build_doctor_analysis(
    month: str,
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    theme_css: str,
    common_js: str,
    use_real_names: bool = False,
) -> Path:
    """評価対象の全診療科について、医師別一覧HTMLを1枚生成する。"""
    classifier = DeptClassifier(classification_path)
    data = load_aggregated_data(aggregated_root, month)

    sections: list[dict[str, Any]] = []
    for info in classifier.evaluation_targets():
        rows = _dept_doctor_rows(data.doctor_summary, info.name, use_real_names=use_real_names)
        if not rows:
            continue
        sections.append({
            "name": info.name,
            "type_label": _type_label(info.type),
            "anchor": f"dept-{info.code}",
            "rows": rows,
            "doctor_count": len(rows),
            "total": sum(r["total"] for r in rows),
        })

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    body = env.get_template("doctor_analysis.html").render(
        month=month,
        sections=sections,
        common_js=common_js,
    )
    html = env.get_template("base.html").render(
        title=f"医師別 深掘り {month}",
        site_title=f"医師別 分析 ({month})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("医師別分析HTML出力: %s", output_path)
    return output_path
