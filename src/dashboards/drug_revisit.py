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
from src.core.observations import (
    drug_revisit_facts_dict,
    drug_revisit_fallback_comment,
    extract_drug_revisit_observation,
)

logger = logging.getLogger(__name__)

TOP_N_PER_DEPT = 15

# LLM 観察コメント生成の依頼文（プロンプトを変えたら _OBS_PROMPT_VERSION も上げる）
_DRUG_REVISIT_INSTRUCTION = (
    "この診療科の薬再診候補スコア結果について、再編会議の議題作りに使える観察コメントを"
    "1〜2 文で書いてください。スコア≥60 の医師×枠の集中度合い、最上位の特徴（短時間再診比率と"
    "再診件数）、そして「逆紹介検討」「枠運用見直し」など議論の方向を簡潔に示してください。"
)


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


def _attach_observations(
    sections: list[dict[str, Any]],
    month: str,
    llm_client: Any | None,
    cache_root: Path | None,
) -> None:
    """各セクションに observation コメント（1〜2 文）を付与する。

    LLM クライアントがあれば LLM 生成、無ければ Python 定型文。
    どちらの場合も observation キーが必ず文字列で入る。
    """
    cache_dir = cache_root / month if cache_root is not None else None
    for s in sections:
        obs = extract_drug_revisit_observation(s)
        facts = drug_revisit_facts_dict(obs)

        def _fb(o: Any = obs) -> str:
            return drug_revisit_fallback_comment(o)

        if llm_client is None:
            s["observation"] = _fb()
            continue

        s["observation"] = llm_client.generate_observation(
            section="drug_revisit",
            facts=facts,
            instruction=_DRUG_REVISIT_INSTRUCTION,
            fallback=_fb,
            cache_dir=cache_dir,
            cache_subkey=s["code"],
        )


def build_drug_revisit(
    months: list[str],
    aggregated_root: Path,
    templates_dir: Path,
    output_path: Path,
    classification_path: Path,
    all_months: list[str],
    default_month: str | None = None,
    llm_client: Any | None = None,
    llm_cache_root: Path | None = None,
) -> Path:
    """薬再診候補スコア ダッシュボードHTMLを1枚生成する（全月埋め込み）。

    Args:
        llm_client: 観察コメント生成用 LLMClient。None の場合は定型文のみ。
        llm_cache_root: 観察コメントのキャッシュ保存ルート（data/llm_cache/drug_revisit など）。
    """
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
        _attach_observations(sections, m, llm_client, llm_cache_root)
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
    sorted_desc = sorted(all_months, reverse=True)
    latest = sorted_desc[0] if sorted_desc else default_month

    html = env.get_template("drug_revisit.html").render(
        # ===== グローバルレイアウト共通コンテキスト =====
        title=f"薬再診候補スコア {default_month}",
        active="drug",
        current_month=None,
        latest_month=latest,
        current_code=None,
        all_months=sorted_desc,
        root_prefix="",
        breadcrumb=None,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        # ===== ページ固有 =====
        months=sorted_months,
        default_month=default_month,
        months_data=months_data,
        export_csv_by_month_json=json.dumps(export_csv_by_month, ensure_ascii=False),
        short_exam_threshold=DRUG_REVISIT_SHORT_EXAM_MIN,
        min_records=DRUG_REVISIT_MIN_RECORDS,
        top_n=TOP_N_PER_DEPT,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("薬再診候補スコア出力: %s (%d ヶ月)", output_path, len(sorted_months))
    return output_path
