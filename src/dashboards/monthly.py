"""月次管理ダッシュボード生成。

集計CSV（data/aggregated/YYYY-MM/）を読み、6ヶ月分のトレンドを構築して
templates/monthly.html にデータ埋込みで出力する。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.classify import DeptClassifier
from src.core.data_loader import AggregatedData, load_aggregated_data, load_last_n_months
from src.core.highlights import extract_highlights
from src.llm_client import LLMClient

logger = logging.getLogger(__name__)

RARE_SLOT_THRESHOLD = 5
DOCTOR_LIMIT = 10


def _load_targets(targets_path: Path) -> dict[str, dict[str, float]]:
    """dept_targets.csv を読み込む。存在しなければ空。"""
    if not targets_path.exists():
        logger.warning("目標ファイル未検出: %s（自動算出のみ）", targets_path)
        return {}
    df = pd.read_csv(targets_path, encoding="utf-8-sig")
    targets: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        targets[str(row["診療科名"])] = {
            "sho_target": int(row.get("初診目標_月", 0) or 0),
            "kus_target": int(row.get("薬のみ再診_目標", 0) or 0),
            "sps_target": float(row.get("再診初診比率_目標", 0) or 0),
        }
    return targets


def _build_tz_detail(data: AggregatedData, dept: str) -> dict[str, int]:
    """当月の時間帯別件数を返す。"""
    sub = data.dept_timezone[data.dept_timezone["診療科名"] == dept]
    def _sum(zone: str) -> int:
        return int(sub[sub["時間帯ゾーン"] == zone]["件数"].sum())
    return {
        "am": _sum("午前(〜12時)"),
        "pm1": _sum("午後前半(12-15時)"),
        "pm2": _sum("午後後半(15-17時)"),
        "eve": _sum("夕方以降(17時〜)"),
    }


def _build_doctor_detail(
    data: AggregatedData, dept: str, use_real_names: bool = False
) -> list[dict[str, Any]]:
    """当月の医師別件数（上位N名）を返す。"""
    sub = data.doctor_summary[data.doctor_summary["診療科名"] == dept]
    if sub.empty:
        return []
    agg = (
        sub.groupby("予約担当者匿名ID")
        .apply(lambda g: pd.Series({
            "total": int(g["件数"].sum()),
            "sho": int(g[g["初再診区分"] == "初診"]["件数"].sum()),
        }))
        .sort_values("total", ascending=False)
        .head(DOCTOR_LIMIT)
        .reset_index()
    )
    doctor_ids = agg["予約担当者匿名ID"].tolist()
    rows = []
    for i, r in enumerate(agg.itertuples(index=False), start=1):
        rate = round(r.sho / r.total * 100, 1) if r.total > 0 else 0.0
        if use_real_names:
            n = str(doctor_ids[i - 1])
        else:
            label = chr(64 + i) if i <= 26 else str(i)
            n = f"医師{label}"
        rows.append({"n": n, "total": int(r.total), "sho": int(r.sho), "rate": rate})
    return rows


def _build_slot_detail(
    slot_frames: list[pd.DataFrame], dept: str
) -> tuple[list[dict], list[dict], list[dict]]:
    """6ヶ月分の予約枠データから rare / saijyu / kairi を返す。"""
    combined = pd.concat(slot_frames, ignore_index=True)
    sub = combined[combined["診療科名"] == dept]
    if sub.empty:
        return [], [], []

    pivot = (
        sub.groupby(["予約名称", "初再診区分"], dropna=False)["件数"]
        .sum().unstack(fill_value=0)
    )
    sho_col = pivot.get("初診", pd.Series(0, index=pivot.index))
    sai_col = pivot.get("再診", pd.Series(0, index=pivot.index))
    total_col = sho_col + sai_col

    rare, saijyu, kairi = [], [], []
    for name in total_col.index:
        t = int(total_col[name])
        s = int(sho_col.get(name, 0))
        r = round(s / t * 100, 1) if t > 0 else 0.0
        entry = {"n": str(name), "t": t, "r": r}
        if t < RARE_SLOT_THRESHOLD:
            rare.append(entry)
        if t >= 20:
            saijyu.append(entry)
        if any(kw in str(name) for kw in ("初診",)) and t > 0:
            sai = int(sai_col.get(name, 0))
            if t > 0 and sai / t >= 0.5:
                kairi.append(entry)

    rare.sort(key=lambda x: x["t"])
    saijyu.sort(key=lambda x: -x["t"])
    kairi.sort(key=lambda x: -x["t"])
    return rare[:5], saijyu[:5], kairi[:5]


def _build_dashboard_data(
    aggregated_root: Path,
    month: str,
    classifier: DeptClassifier,
    user_targets: dict[str, dict[str, float]],
    n_months: int = 6,
    use_real_names: bool = False,
) -> dict[str, Any]:
    """対象月を最終月として、過去n_months分のトレンドデータを構築。"""
    months = load_last_n_months(aggregated_root, month, n=n_months)
    if not months:
        raise ValueError(f"集計ディレクトリが見つかりません: {aggregated_root}")

    kpi_frames: list[pd.DataFrame] = []
    rr_frames: list[pd.DataFrame] = []
    slot_frames: list[pd.DataFrame] = []
    all_data: list[AggregatedData] = []

    for m in months:
        d = load_aggregated_data(aggregated_root, m)
        all_data.append(d)
        kpi_frames.append(d.referral_kpi)
        rr_frames.append(d.reverse_referral)
        slot_frames.append(d.slot_analysis)

    kpi = pd.concat(kpi_frames, ignore_index=True)
    rr = pd.concat(rr_frames, ignore_index=True)
    current_data = all_data[-1]  # 当月

    if kpi.empty:
        raise ValueError("集計CSVに月データがありません")

    month_labels = [m.split("-")[1].lstrip("0") + "月" for m in months]

    rr_best = rr[
        (rr["初再診区分"] == "再診")
        & (rr["紹介状有無"] == "紹介状無し")
        & (rr["併科受診フラグ"] == "無")
        & (rr["診察時間_階級"].isin(["0-4分", "5-9分"]))
        & (rr["診察前検査フラグ"] == "なし")
    ]
    kusuri = rr[(rr["診療区分"] == "薬のみ") & (rr["初再診区分"] == "再診")]

    depts_data: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}

    for dept_name in kpi["診療科名"].unique():
        if not classifier.is_evaluation_target(dept_name):
            continue
        k = kpi[kpi["診療科名"] == dept_name].set_index("月").reindex(months).fillna(0)
        total = k["総件数"].astype(int).tolist()
        sho = k["初診件数"].astype(int).tolist()
        sai = k["再診件数"].astype(int).tolist()

        kus_m = (
            kusuri[kusuri["診療科名"] == dept_name]
            .groupby("月")["件数"].sum()
            .reindex(months).fillna(0).astype(int).tolist()
        )
        cand_m = (
            rr_best[rr_best["診療科名"] == dept_name]
            .groupby("月")["件数"].sum()
            .reindex(months).fillna(0).astype(int).tolist()
        )

        avg_monthly = sum(total) // max(len(months), 1)
        if avg_monthly < 30:
            continue

        sps_m = [round(s / h, 1) if h > 0 else None for s, h in zip(sai, sho)]

        n = len(months)
        avg_sho = sum(sho) / n
        avg_kus = sum(kus_m) / n
        avg_sai = sum(sai) / n
        sps_avg = round(avg_sai / avg_sho, 1) if avg_sho > 0 else 0

        ut = user_targets.get(dept_name, {})
        sho_target = (
            int(ut["sho_target"]) if ut.get("sho_target")
            else (int(avg_sho * 1.10) if avg_sho > 0 else 0)
        )
        kus_target = (
            int(ut["kus_target"]) if ut.get("kus_target")
            else (int(avg_kus * 0.85) if avg_kus > 0 else 0)
        )
        sps_target = (
            float(ut["sps_target"]) if ut.get("sps_target")
            else (round(sps_avg * 0.9, 1) if sps_avg > 0 else 0)
        )

        dept_type = classifier.get_type(dept_name)
        type_key = {"外科系": "geka", "内科系": "naika"}.get(dept_type, "other")

        # 当月（最終インデックス）の値を _apr フィールドとして追加
        depts_data.append({
            "name": dept_name,
            "type": type_key,
            "avg_monthly": avg_monthly,
            "sho_m": sho,
            "sai_m": sai,
            "kus_m": kus_m,
            "cand_m": cand_m,
            "total_m": total,
            "sps_m": sps_m,
            "sho_target": sho_target,
            "kus_target": kus_target,
            "sps_target": sps_target,
            # テンプレートが参照する当月単体フィールド
            "sho_apr": sho[-1],
            "sai_apr": sai[-1],
            "kus_apr": kus_m[-1],
            "cand_apr": cand_m[-1],
            "total_apr": total[-1],
            "sps_apr": sps_m[-1],
        })

        # detail: 当月の時間帯・医師・枠ランキング
        rare, saijyu, kairi = _build_slot_detail(slot_frames, dept_name)
        detail[dept_name] = {
            "tz": _build_tz_detail(current_data, dept_name),
            "doctors": _build_doctor_detail(current_data, dept_name, use_real_names=use_real_names),
            "rare": rare,
            "saijyu": saijyu,
            "kairi": kairi,
        }

    depts_data.sort(key=lambda x: x["avg_monthly"], reverse=True)

    total_sho_monthly = [sum(d["sho_m"][i] for d in depts_data) for i in range(len(months))]
    total_sai_monthly = [sum(d["sai_m"][i] for d in depts_data) for i in range(len(months))]
    total_kus_monthly = [sum(d["kus_m"][i] for d in depts_data) for i in range(len(months))]
    total_cand_monthly = [sum(d["cand_m"][i] for d in depts_data) for i in range(len(months))]
    total_monthly = [sum(d["total_m"][i] for d in depts_data) for i in range(len(months))]

    return {
        "months": months,
        "monthLabels": month_labels,
        "depts": depts_data,
        "detail": detail,
        "total_sho_monthly": total_sho_monthly,
        "total_sai_monthly": total_sai_monthly,
        "total_kus_monthly": total_kus_monthly,
        "total_cand_monthly": total_cand_monthly,
        "total_monthly": total_monthly,
        # テンプレートが参照する当月単体フィールド（配列の末尾）
        "total_sho_apr": total_sho_monthly[-1],
        "total_sai_apr": total_sai_monthly[-1],
        "total_kus_apr": total_kus_monthly[-1],
        "global_sho_target": sum(d["sho_target"] for d in depts_data),
        "global_kus_target": sum(d["kus_target"] for d in depts_data),
        "generated_at": datetime.now().isoformat(),
    }


def _render(
    template_path: Path,
    output_path: Path,
    data: dict[str, Any],
    highlights: dict[str, Any],
) -> None:
    """テンプレートのプレースホルダに data/highlights/メタ情報を埋め込む。"""
    html = template_path.read_text(encoding="utf-8")
    html = html.replace(
        "{{DASHBOARD_DATA_JSON}}",
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    )
    html = html.replace(
        "{{HIGHLIGHTS_JSON}}",
        json.dumps(highlights, ensure_ascii=False, separators=(",", ":")),
    )
    html = html.replace(
        "{{GENERATED_AT}}",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    html = html.replace("{{CURRENT_MONTH}}", data["monthLabels"][-1])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("HTML出力: %s (%d chars)", output_path, len(html))


def build_monthly_dashboard(
    month: str,
    output_path: Path,
    aggregated_root: Path,
    template_path: Path,
    classification_path: Path,
    targets_path: Path,
    llm_config_path: Path,
    use_llm: bool = True,
    use_real_names: bool = False,
) -> None:
    """月次ダッシュボードを生成する。

    Args:
        month: "YYYY-MM" 形式
        output_path: 出力HTMLパス
        aggregated_root: data/aggregated/ のパス
        template_path: templates/monthly.html のパス
        classification_path: config/dept_classification.csv
        targets_path: config/dept_targets.csv
        llm_config_path: config/llm_config.yaml
        use_llm: FalseならLLMを呼ばず定型文で生成
        use_real_names: Trueなら医師実名を表示（ローカル専用）
    """
    classifier = DeptClassifier(classification_path)
    user_targets = _load_targets(targets_path)

    data = _build_dashboard_data(aggregated_root, month, classifier, user_targets, use_real_names=use_real_names)
    candidates = extract_highlights(data["depts"])

    llm = LLMClient(llm_config_path, enabled=use_llm)
    highlights = llm.generate_highlights(candidates)

    _render(template_path, output_path, data, highlights)
