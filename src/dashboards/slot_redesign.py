"""予約枠 再設計提案ダッシュボード生成。

`07_slot_analysis.csv` を用いて、診療科ごとの
 - A枠シェア（紹介状あり初診 ÷ 初診）
 - 命名乖離枠（「初診」系名称なのに再診で多用／「紹介」系名称なのに紹介状無しで多用）
 - 稀用枠（当月件数が閾値未満）
を抽出し、1枚のHTMLで俯瞰できるようにする。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.classify import DeptClassifier
from src.core.data_loader import load_aggregated_data

logger = logging.getLogger(__name__)

RARE_THRESHOLD = 5
SHO_KEYWORDS = ("初診",)
SHOKAI_KEYWORDS = ("紹介",)
SAI_MAJORITY_RATIO = 0.5
NO_SHOKAI_MAJORITY_RATIO = 0.7


@dataclass
class _DeptSlotStats:
    name: str
    type_label: str
    total: int
    sho: int
    a_count: int
    a_share: float
    rare_slots: int
    naming_issues: int


def _slot_records_for_dept(slot_df: pd.DataFrame, dept: str) -> pd.DataFrame:
    sub = slot_df[slot_df["診療科名"] == dept]
    if sub.empty:
        return sub
    agg = (
        sub.groupby(["予約名称", "初再診区分", "紹介状有無"], dropna=False)["件数"]
        .sum().reset_index()
    )
    return agg


def _classify_slot(pivot: pd.Series) -> list[str]:
    """単一スロット（予約名称単位）に対する検出タグ一覧。"""
    flags: list[str] = []
    name = str(pivot["予約名称"])
    total = int(pivot["total"])
    sho = int(pivot["sho"])
    sai = int(pivot["sai"])
    shokai_sho = int(pivot["shokai_sho"])
    shokai_all = int(pivot["shokai_all"])

    if total < RARE_THRESHOLD:
        flags.append("稀用")

    if any(kw in name for kw in SHO_KEYWORDS) and total > 0:
        if sai / total >= SAI_MAJORITY_RATIO:
            flags.append("命名乖離(初診名だが再診多用)")

    if any(kw in name for kw in SHOKAI_KEYWORDS) and total > 0:
        no_shokai = total - shokai_all
        if no_shokai / total >= NO_SHOKAI_MAJORITY_RATIO:
            flags.append("命名乖離(紹介名だが紹介状無し多用)")

    if shokai_sho >= RARE_THRESHOLD and sho > 0 and shokai_sho / sho >= 0.5 and "紹介" not in name and "初診" not in name:
        flags.append("A枠候補(未命名)")

    return flags


def _build_slot_table(slot_df: pd.DataFrame, dept: str) -> list[dict[str, Any]]:
    agg = _slot_records_for_dept(slot_df, dept)
    if agg.empty:
        return []

    pivot = agg.pivot_table(
        index="予約名称",
        columns=["初再診区分", "紹介状有無"],
        values="件数",
        aggfunc="sum",
        fill_value=0,
    )

    rows: list[dict[str, Any]] = []
    for name, r in pivot.iterrows():
        sho = int(
            r.get(("初診", "紹介状あり"), 0) + r.get(("初診", "紹介状無し"), 0)
        )
        sai = int(
            r.get(("再診", "紹介状あり"), 0) + r.get(("再診", "紹介状無し"), 0)
        )
        shokai_sho = int(r.get(("初診", "紹介状あり"), 0))
        shokai_all = int(
            r.get(("初診", "紹介状あり"), 0) + r.get(("再診", "紹介状あり"), 0)
        )
        total = sho + sai
        flags = _classify_slot(pd.Series({
            "予約名称": name,
            "total": total,
            "sho": sho,
            "sai": sai,
            "shokai_sho": shokai_sho,
            "shokai_all": shokai_all,
        }))
        rows.append({
            "name": str(name),
            "total": total,
            "sho": sho,
            "sai": sai,
            "shokai_sho": shokai_sho,
            "flags": flags,
        })

    rows.sort(key=lambda x: x["total"], reverse=True)
    return rows


def _dept_stats(
    slot_df: pd.DataFrame, rows: list[dict[str, Any]], dept: str, dept_type: str
) -> _DeptSlotStats:
    total = sum(r["total"] for r in rows)
    sho = sum(r["sho"] for r in rows)
    a_count = sum(r["shokai_sho"] for r in rows)
    a_share = round(a_count / sho * 100, 2) if sho else 0.0
    rare = sum(1 for r in rows if "稀用" in r["flags"])
    naming = sum(1 for r in rows if any("命名乖離" in f for f in r["flags"]))
    return _DeptSlotStats(
        name=dept,
        type_label=dept_type,
        total=total,
        sho=sho,
        a_count=a_count,
        a_share=a_share,
        rare_slots=rare,
        naming_issues=naming,
    )


def _render(
    env: Environment,
    *,
    month: str,
    dept_sections: list[dict[str, Any]],
    overview: list[dict[str, Any]],
    theme_css: str,
    common_js: str,
) -> str:
    body = env.get_template("slot_redesign.html").render(
        month=month,
        overview=overview,
        dept_sections=dept_sections,
        rare_threshold=RARE_THRESHOLD,
        common_js=common_js,
    )
    return env.get_template("base.html").render(
        title=f"予約枠 再設計 {month}",
        site_title=f"予約枠 再設計提案 ({month})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )


def _type_label(type_key: str) -> str:
    return {"外科系": "外科", "内科系": "内科"}.get(type_key, "その他")


def build_slot_redesign(
    month: str,
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    theme_css: str,
    common_js: str,
) -> Path:
    """評価対象の全診療科について、予約枠再設計HTMLを1枚生成する。"""
    classifier = DeptClassifier(classification_path)
    data = load_aggregated_data(aggregated_root, month)

    slot_df = data.slot_analysis
    if "月" in slot_df.columns:
        slot_df = slot_df[slot_df["月"].astype(str) == month]

    dept_sections: list[dict[str, Any]] = []
    overview: list[dict[str, Any]] = []

    for info in classifier.evaluation_targets():
        rows = _build_slot_table(slot_df, info.name)
        if not rows:
            continue
        stats = _dept_stats(slot_df, rows, info.name, _type_label(info.type))
        overview.append({
            "name": stats.name,
            "type_label": stats.type_label,
            "total": f"{stats.total:,}",
            "sho": f"{stats.sho:,}",
            "a_count": f"{stats.a_count:,}",
            "a_share": stats.a_share,
            "rare_slots": stats.rare_slots,
            "naming_issues": stats.naming_issues,
            "anchor": f"dept-{info.code}",
        })
        dept_sections.append({
            "name": info.name,
            "type_label": stats.type_label,
            "anchor": f"dept-{info.code}",
            "a_share": stats.a_share,
            "a_count": stats.a_count,
            "rows": rows,
        })

    overview.sort(key=lambda x: (-x["naming_issues"], -x["rare_slots"], x["name"]))

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    html = _render(
        env,
        month=month,
        dept_sections=dept_sections,
        overview=overview,
        theme_css=theme_css,
        common_js=common_js,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("予約枠再設計HTML出力: %s", output_path)
    return output_path
