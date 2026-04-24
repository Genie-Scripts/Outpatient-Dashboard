"""ハブページ (docs/index.html) 生成。

docs/ 配下のHTMLを走査しつつ、data/aggregated/ から最新月のKPIと
月次トレンドを読み、経営層が一目でサマリを把握できる
ダッシュボード風トップページを Jinja2 でレンダリングする。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

_MONTH_FILE_RE = re.compile(r"^(\d{4}-\d{2})\.html$")
_MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

_TYPE_ORDER = ["内科系", "外科系", "その他"]
_TYPE_KEY = {"内科系": "naika", "外科系": "geka", "その他": "other"}


def _load_dept_map(classification_path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """診療科コード/科名の両方で引けるメタ情報マップ。"""
    if not classification_path.exists():
        logger.warning("診療科分類CSV未検出: %s", classification_path)
        return {"by_code": {}, "by_name": {}}
    df = pd.read_csv(classification_path, encoding="utf-8-sig")
    by_code: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        meta = {
            "code": str(row["診療科コード"]).strip(),
            "name": str(row["診療科名"]).strip(),
            "type": str(row["タイプ"]).strip(),
            "order": int(row.get("表示順") or 999),
        }
        by_code[meta["code"]] = meta
        by_name[meta["name"]] = meta
    return {"by_code": by_code, "by_name": by_name}


def _list_monthly(docs_dir: Path) -> list[str]:
    """docs/monthly/YYYY-MM.html の月を新→旧で返す。"""
    d = docs_dir / "monthly"
    if not d.exists():
        return []
    months: list[str] = []
    for f in d.iterdir():
        m = _MONTH_FILE_RE.match(f.name)
        if m:
            months.append(m.group(1))
    return sorted(months, reverse=True)


def _list_dept_months(docs_dir: Path) -> list[str]:
    """docs/dept/YYYY-MM/ の月を新→旧で返す。"""
    d = docs_dir / "dept"
    if not d.exists():
        return []
    months = [x.name for x in d.iterdir() if x.is_dir() and _MONTH_DIR_RE.match(x.name)]
    return sorted(months, reverse=True)


def _delta(cur: float, prev: float) -> dict[str, Any]:
    """前月比を {pct, sign} で返す。"""
    if prev == 0:
        return {"pct": 0.0, "sign": "flat"}
    pct = (cur - prev) / prev * 100
    if pct > 2:
        sign = "up"
    elif pct < -2:
        sign = "down"
    else:
        sign = "flat"
    return {"pct": round(pct, 1), "sign": sign}


def _load_trend(aggregated_root: Path, months: list[str]) -> list[dict[str, Any]]:
    """各月の 00_summary.csv を連結してトレンド配列を作る。"""
    rows: list[dict[str, Any]] = []
    for m in sorted(months):
        p = aggregated_root / m / "00_summary.csv"
        if not p.exists():
            continue
        s = pd.read_csv(p, encoding="utf-8-sig").iloc[0]
        sho = int(s["初診件数"])
        total = int(s["総件数"])
        ref = int(s["紹介状あり"])
        miraiin = int(s["未来院件数"])
        rows.append({
            "month": m,
            "total": total,
            "sho": sho,
            "sai": int(s["再診件数"]),
            "ref_rate": round(ref / sho * 100, 1) if sho else 0.0,
            "miraiin": miraiin,
            "miraiin_rate": round(miraiin / total * 100, 1) if total else 0.0,
            "doctors": int(s.get("医師数", 0) or 0),
        })
    return rows


def _build_kpis(trend: list[dict[str, Any]]) -> dict[str, Any] | None:
    """トレンドから最新月のKPIブロックを組み立てる。"""
    if not trend:
        return None
    latest = trend[-1]
    prev = trend[-2] if len(trend) >= 2 else {}
    return {
        "total": latest["total"],
        "total_delta": _delta(latest["total"], prev.get("total", 0)),
        "sho": latest["sho"],
        "sho_delta": _delta(latest["sho"], prev.get("sho", 0)),
        "sai": latest["sai"],
        "ref_rate": latest["ref_rate"],
        "miraiin_rate": latest["miraiin_rate"],
        "miraiin_rate_delta": _delta(latest["miraiin_rate"], prev.get("miraiin_rate", 0)),
        "doctors": latest["doctors"],
    }


def _load_dept_kpi(aggregated_root: Path, month: str | None) -> pd.DataFrame:
    """月次診療科別KPI（10_referral_kpi.csv）を読む。無ければ空DF。"""
    if not month:
        return pd.DataFrame()
    p = aggregated_root / month / "10_referral_kpi.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, encoding="utf-8-sig")


def _build_dept_cards(
    latest_kpi: pd.DataFrame,
    prev_kpi: pd.DataFrame,
    dept_map: dict[str, dict[str, dict[str, Any]]],
    latest_month: str,
) -> list[dict[str, Any]]:
    """最新月の科別カードをタイプ別にグルーピングして返す。"""
    by_name_meta = dept_map.get("by_name", {})
    prev_map = {r["診療科名"]: r for _, r in prev_kpi.iterrows()} if len(prev_kpi) else {}

    by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in _TYPE_ORDER}
    for _, r in latest_kpi.iterrows():
        name = str(r["診療科名"])
        meta = by_name_meta.get(name)
        if not meta:
            continue
        href = f"dept/{latest_month}/{meta['code']}.html"
        total = int(r["総件数"])
        prev_total = int(prev_map.get(name, {}).get("総件数", 0)) if name in prev_map else 0
        card = {
            "code": meta["code"],
            "name": name,
            "type_key": _TYPE_KEY.get(meta["type"], "other"),
            "href": href,
            "total": total,
            "sho": int(r["初診件数"]),
            "ref_rate": float(r["紹介率"]),
            "sho_rate": float(r["初診率"]),
            "miraiin_rate": float(r["未来院率"]),
            "delta": _delta(total, prev_total),
            "_order": meta["order"],
        }
        by_type.setdefault(meta["type"], []).append(card)

    groups: list[dict[str, Any]] = []
    for t in _TYPE_ORDER:
        items = sorted(by_type.get(t, []), key=lambda x: (-x["total"], x["_order"]))
        if not items:
            continue
        for it in items:
            it.pop("_order", None)
        groups.append({"group": t, "type_key": _TYPE_KEY[t], "items": items})
    return groups


def _build_past_dept(
    docs_dir: Path,
    dept_months: list[str],
    latest: str | None,
    dept_map: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """最新月を除く過去月の科リスト（折り畳み表示用）。"""
    by_code = dept_map.get("by_code", {})
    result: list[dict[str, Any]] = []
    for m in dept_months:
        if m == latest:
            continue
        month_dir = docs_dir / "dept" / m
        codes = sorted(f.stem for f in month_dir.glob("*.html"))
        if not codes:
            continue
        groups: dict[str, list[dict[str, Any]]] = {t: [] for t in _TYPE_ORDER}
        for code in codes:
            meta = by_code.get(code, {"name": code, "type": "その他", "order": 999})
            groups.setdefault(meta["type"], []).append({
                "code": code,
                "name": meta["name"],
                "href": f"dept/{m}/{code}.html",
                "_order": meta["order"],
            })
        group_list: list[dict[str, Any]] = []
        for t in _TYPE_ORDER:
            items = sorted(groups.get(t, []), key=lambda x: x["_order"])
            if not items:
                continue
            for it in items:
                it.pop("_order", None)
            group_list.append({"group": t, "type_key": _TYPE_KEY[t], "items": items})
        total = sum(len(g["items"]) for g in group_list)
        if total:
            result.append({"month": m, "total": total, "groups": group_list})
    return result


def _build_extras(docs_dir: Path) -> list[dict[str, str]]:
    """補助ダッシュボードの存在確認つきリスト。"""
    return [
        {
            "title": "予約枠 再設計提案",
            "desc": "全科の予約枠内訳と再設計シミュレーション",
            "href": "slot_redesign.html" if (docs_dir / "slot_redesign.html").exists() else "",
        },
        {
            "title": "医師別 深掘り分析",
            "desc": "医師ごとの外来パフォーマンス分析",
            "href": "doctor_analysis.html" if (docs_dir / "doctor_analysis.html").exists() else "",
        },
        {
            "title": "曜日×時間帯ヒートマップ",
            "desc": "看護師配置最適化のための到着・同時並行診察数マップ",
            "href": "hourly_heatmap.html" if (docs_dir / "hourly_heatmap.html").exists() else "",
        },
        {
            "title": "医師×時間帯ヒートマップ",
            "desc": "診療科ごとの医師別 曜日×時間帯 出勤パターン（出勤頻度率）",
            "href": "doctor_heatmap.html" if (docs_dir / "doctor_heatmap.html").exists() else "",
        },
        {
            "title": "薬再診候補スコア",
            "desc": "短時間再診比率等から逆紹介候補を抽出",
            "href": "drug_revisit.html" if (docs_dir / "drug_revisit.html").exists() else "",
        },
    ]


def _build_context(
    docs_dir: Path,
    aggregated_root: Path,
    classification_path: Path,
) -> dict[str, Any]:
    """テンプレートに渡す全コンテキストを組み立てる。"""
    dept_map = _load_dept_map(classification_path)
    monthly_months = _list_monthly(docs_dir)
    dept_months = _list_dept_months(docs_dir)
    all_months = sorted(set(monthly_months) | set(dept_months))
    latest = all_months[-1] if all_months else None
    prev_month = all_months[-2] if len(all_months) >= 2 else None

    trend = _load_trend(aggregated_root, all_months) if all_months else []
    kpis = _build_kpis(trend)

    dept_groups: list[dict[str, Any]] = []
    if latest:
        latest_kpi = _load_dept_kpi(aggregated_root, latest)
        prev_kpi = _load_dept_kpi(aggregated_root, prev_month)
        dept_groups = _build_dept_cards(latest_kpi, prev_kpi, dept_map, latest)

    monthly_links = [
        {"month": m, "href": f"monthly/{m}.html", "is_latest": m == latest}
        for m in monthly_months
    ]

    past_dept_by_month = _build_past_dept(docs_dir, dept_months, latest, dept_map)
    data_period = {"from": all_months[0], "to": all_months[-1]} if all_months else None

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_period": data_period,
        "latest_month": latest,
        "monthly_links": monthly_links,
        "monthly_count": len(monthly_links),
        "dept_groups": dept_groups,
        "dept_total_count": sum(len(g["items"]) for g in dept_groups),
        "past_dept_by_month": past_dept_by_month,
        "extras": _build_extras(docs_dir),
        "kpis": kpis,
        "trend_json": json.dumps(trend, ensure_ascii=False),
    }


def build_hub_page(
    docs_dir: Path,
    templates_dir: Path,
    aggregated_root: Path,
    classification_path: Path,
    theme_css: str = "",
) -> Path:
    """docs/index.html を生成する。

    Args:
        docs_dir: docs/ のパス（入出力両用）
        templates_dir: Jinja2テンプレ格納ディレクトリ
        aggregated_root: data/aggregated/ のパス
        classification_path: config/dept_classification.csv
        theme_css: 互換性のため残置。標準テンプレは未使用（インラインCSS）

    Returns:
        書き出した index.html の Path
    """
    context = _build_context(docs_dir, aggregated_root, classification_path)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("index.html").render(**context)

    output = docs_dir / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    logger.info("ハブページ出力: %s (%d chars)", output, len(html))
    return output
