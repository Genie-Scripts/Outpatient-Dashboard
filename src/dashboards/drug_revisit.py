"""薬再診候補スコア ダッシュボード生成。

`13_drug_revisit_score.csv` を用いて、医師×枠×月ごとの
 - 短時間再診比率（診察時間 ≤ 4分）
 - 紹介状なし再診比率
 - 診察時間中央値_再診（短いほど薬再診示唆）
を合成した合成スコアをランキング表示し、
地域連携室が開業医逆紹介の対象リスト作成に使えるようにする。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.aggregate import DRUG_REVISIT_MIN_RECORDS, DRUG_REVISIT_SHORT_EXAM_MIN
from src.core.classify import DeptClassifier

logger = logging.getLogger(__name__)

TOP_N_PER_DEPT = 15


def _load_score(aggregated_root: Path, month: str) -> pd.DataFrame:
    p = aggregated_root / month / "13_drug_revisit_score.csv"
    if not p.exists():
        raise FileNotFoundError(f"13_drug_revisit_score.csv が存在しません: {p}")
    return pd.read_csv(p, encoding="utf-8-sig")


def _format_row(r: pd.Series) -> dict[str, Any]:
    score = r["スコア"]
    return {
        "medic": str(r["医師匿名ID"]),
        "slot": str(r["予約名称"]),
        "month": str(r["月"]),
        "sai": int(r["再診件数"]),
        "short_ratio": round(float(r["短時間再診比率"]) * 100, 1),
        "no_shokai_ratio": round(float(r["紹介状なし再診比率"]) * 100, 1),
        "median_exam": float(r["診察時間中央値_再診"]) if pd.notna(r["診察時間中央値_再診"]) else None,
        "score": None if pd.isna(score) else round(float(score), 1),
        "scoreable": pd.notna(score),
    }


def _build_dept_sections(
    score_df: pd.DataFrame, month: str, classifier: DeptClassifier
) -> list[dict[str, Any]]:
    month_df = score_df[score_df["月"].astype(str) == month]
    sections: list[dict[str, Any]] = []
    for info in classifier.evaluation_targets():
        sub = month_df[month_df["診療科名"] == info.name]
        if sub.empty:
            continue
        scoreable = sub[sub["スコア"].notna()].sort_values("スコア", ascending=False)
        top = scoreable.head(TOP_N_PER_DEPT)
        rows = [_format_row(r) for _, r in top.iterrows()]

        sai_total = int(sub["再診件数"].sum())
        short_total = int(sub["短時間再診件数"].sum())
        no_shokai_total = int(sub["紹介状なし再診件数"].sum())
        high_score = int((sub["スコア"] >= 60).sum())

        sections.append({
            "name": info.name,
            "code": info.code,
            "type_label": {"外科系": "外科", "内科系": "内科"}.get(info.type, "その他"),
            "anchor": f"drev-{info.code}",
            "sai_total": sai_total,
            "short_ratio": round(short_total / sai_total * 100, 1) if sai_total else 0.0,
            "no_shokai_ratio": round(no_shokai_total / sai_total * 100, 1) if sai_total else 0.0,
            "scoreable_count": int(sub["スコア"].notna().sum()),
            "high_score_count": high_score,
            "rows": rows,
        })
    sections.sort(key=lambda s: (-s["high_score_count"], -s["short_ratio"], s["name"]))
    return sections


def _build_overview(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": s["name"],
            "type_label": s["type_label"],
            "anchor": s["anchor"],
            "sai_total": f"{s['sai_total']:,}",
            "short_ratio": s["short_ratio"],
            "no_shokai_ratio": s["no_shokai_ratio"],
            "high_score_count": s["high_score_count"],
        }
        for s in sections
    ]


def _build_export_csv(sections: list[dict[str, Any]]) -> str:
    """地域連携室提出用CSV（data URL埋め込み用）。"""
    lines = ["診療科,医師匿名ID,予約名称,再診件数,短時間再診比率(%),紹介状なし再診比率(%),診察時間中央値(分),スコア"]
    for s in sections:
        for r in s["rows"]:
            median = "" if r["median_exam"] is None else f"{r['median_exam']:.1f}"
            score = "" if r["score"] is None else f"{r['score']:.1f}"
            lines.append(
                f"{s['name']},{r['medic']},{r['slot']},{r['sai']},"
                f"{r['short_ratio']},{r['no_shokai_ratio']},{median},{score}"
            )
    return "\n".join(lines)


def build_drug_revisit(
    months: list[str],
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    theme_css: str,
    common_js: str,
    default_month: str | None = None,
) -> Path:
    """薬再診候補スコア ダッシュボードHTMLを1枚生成する（全月埋め込み）。"""
    classifier = DeptClassifier(classification_path)
    if not months:
        raise ValueError("months が空です")

    sorted_months = sorted(months)
    default_month = default_month or sorted_months[-1]

    months_data: list[dict[str, Any]] = []
    export_csv_by_month: dict[str, str] = {}
    for m in sorted_months:
        score_df = _load_score(aggregated_root, m)
        sections = _build_dept_sections(score_df, m, classifier)
        overview = _build_overview(sections)
        months_data.append({
            "month": m,
            "overview": overview,
            "sections": sections,
        })
        export_csv_by_month[m] = _build_export_csv(sections)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    body = env.get_template("drug_revisit.html").render(
        months=sorted_months,
        default_month=default_month,
        months_data=months_data,
        export_csv_by_month_json=json.dumps(export_csv_by_month, ensure_ascii=False),
        short_exam_threshold=DRUG_REVISIT_SHORT_EXAM_MIN,
        min_records=DRUG_REVISIT_MIN_RECORDS,
        top_n=TOP_N_PER_DEPT,
        common_js=common_js,
    )
    html = env.get_template("base.html").render(
        title=f"薬再診候補スコア {default_month}",
        site_title=f"薬再診候補スコア ({sorted_months[0]} 〜 {sorted_months[-1]})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        theme_css=theme_css,
        content=body,
        scripts="",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("薬再診候補スコア出力: %s (%d ヶ月)", output_path, len(sorted_months))
    return output_path
