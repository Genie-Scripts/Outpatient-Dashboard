"""外来枠×時間帯ヒートマップ（集計 + ダッシュボード）の単体テスト。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.aggregate import _agg_slot_hourly, _preprocess
from src.core.classify import DeptClassifier
from src.dashboards.slot_heatmap import (
    _build_dataset,
    _build_dept_series,
    build_slot_heatmap,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _base_row(
    date: str = "2026-03-02",
    uketsuke: str = "09:00:00",
    kaishi: str = "09:05:00",
    shuryo: str = "09:12:00",
    shinsatsu_jikan: float = 7.0,
    dept: str = "泌尿器科",
    doctor: str = "DR_U001",
    slot: str = "再診枠A",
    kubun: str = "再診",
    shokai: str = "紹介状無し",
) -> dict:
    return {
        "予約日": date,
        "受付時刻": uketsuke,
        "開始時刻": kaishi,
        "終了時刻": shuryo,
        "会計終了時刻": "",
        "診察待時間": 0,
        "診察時間": shinsatsu_jikan,
        "会計待時間": 0,
        "入外区分": "外来",
        "予約名称": slot,
        "予約種別": "",
        "診療科名": dept,
        "オーダー診療科名": dept,
        "部屋番号": "A1",
        "予約担当者ID": doctor,
        "予約フラグ": "予約",
        "診療区分": "",
        "診察前検査フラグ": "無",
        "併科受診フラグ": "無",
        "併科診療科略称名1": "",
        "初再診区分": kubun,
        "来院区分": "",
        "診療受付区分": "来院",
        "入院日": "",
        "入院時間": "",
        "入院時病棟": "",
        "退院日": "",
        "退院時病棟": "",
        "紹介状有無": shokai,
        "予約担当者匿名ID": doctor,
    }


def test_agg_slot_hourly_frequency_and_count() -> None:
    mondays = ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    rows = []
    for d in mondays:
        rows.append(_base_row(date=d, kaishi="09:00:00", shuryo="09:15:00", slot="再診枠A"))
    # 別枠が2/4日のみ09:30bin
    rows.append(_base_row(date="2026-03-02", kaishi="09:30:00", shuryo="09:45:00", slot="新患枠", kubun="初診"))
    rows.append(_base_row(date="2026-03-16", kaishi="09:30:00", shuryo="09:45:00", slot="新患枠", kubun="初診"))
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_slot_hourly(proc)

    zentai = out[out["区分"] == "全体"]
    a_0900 = zentai[
        (zentai["予約名称"] == "再診枠A")
        & (zentai["曜日"] == 0)
        & (zentai["bin_idx"] == 2)
    ]
    assert not a_0900.empty
    r = a_0900.iloc[0]
    assert r["稼働日数"] == 4
    assert r["該当日数"] == 4
    assert r["稼働頻度率"] == 1.0
    assert r["件数合計"] == 4
    assert r["実診察分数"] == 60.0

    b_0930 = zentai[
        (zentai["予約名称"] == "新患枠")
        & (zentai["曜日"] == 0)
        & (zentai["bin_idx"] == 3)
    ]
    r = b_0930.iloc[0]
    assert r["稼働日数"] == 2
    assert r["稼働頻度率"] == 0.5
    assert r["件数合計"] == 2


def test_agg_slot_hourly_empty_when_no_valid() -> None:
    rows = [_base_row(kaishi="00:00:01", shuryo="00:00:10", shinsatsu_jikan=0.1)]
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_slot_hourly(proc)
    assert out.empty
    assert list(out.columns) == [
        "診療科名", "予約名称", "区分", "曜日", "bin_idx", "bin_label",
        "稼働日数", "該当日数", "稼働頻度率",
        "件数合計", "実診察分数", "件数_日平均", "実診察分数_日平均",
    ]


def _hourly_row(
    dept: str, slot: str, cat: str, wd: int, bi: int,
    freq: float, count: float, duration: float,
    shukkin: int = 1, days: int = 4,
) -> dict:
    return {
        "診療科名": dept,
        "予約名称": slot,
        "区分": cat,
        "曜日": wd,
        "bin_idx": bi,
        "bin_label": f"{8 + bi // 2:02d}:{(bi % 2) * 30:02d}",
        "稼働日数": shukkin,
        "該当日数": days,
        "稼働頻度率": freq,
        "件数合計": count,
        "実診察分数": duration,
        "件数_日平均": round(count / days, 2) if days else 0.0,
        "実診察分数_日平均": round(duration / days, 1) if days else 0.0,
    }


def _hourly_rows() -> pd.DataFrame:
    rows = []
    # 泌尿器科: 主力枠 再診枠A (12件) と 低稼働枠 追加枠 (2件)
    for cat, count, dur in [("全体", 12, 180.0), ("再診", 10, 150.0), ("初診", 2, 30.0)]:
        rows.append(_hourly_row("泌尿器科", "再診枠A", cat, 0, 2, 1.0, count, dur, shukkin=4))
    for cat, count, dur in [("全体", 2, 20.0), ("再診", 2, 20.0)]:
        rows.append(_hourly_row("泌尿器科", "追加枠", cat, 2, 5, 0.25, count, dur, shukkin=1))
    return pd.DataFrame(rows)


def test_build_dept_series_orders_by_total_asc_and_counts_active_cells() -> None:
    df = _hourly_rows()
    rows = _build_dept_series(df[df["診療科名"] == "泌尿器科"])
    # 低稼働=縮小候補が先頭(昇順)
    assert [r["id"] for r in rows] == ["追加枠", "再診枠A"]
    assert rows[0]["total"] == 2
    assert rows[1]["total"] == 12
    # 全体区分でセルを1つ埋めた → active_cells=1
    assert rows[0]["active_cells"] == 1
    assert rows[1]["active_cells"] == 1
    assert rows[0]["total_cells"] == 6 * 24
    zentai = rows[1]["categories"]["全体"]
    assert zentai["frequency"][0][2] == 1.0
    assert zentai["count"][0][2] == 12.0
    assert zentai["duration"][0][2] == 180.0


def test_build_dataset_keys_and_slot_count() -> None:
    df = _hourly_rows()
    classifier = DeptClassifier(REPO_ROOT / "config" / "dept_classification.csv")
    ds = _build_dataset(df, classifier)
    assert "DEPT_U" in ds
    assert ds["DEPT_U"]["label"] == "泌尿器科"
    assert len(ds["DEPT_U"]["slots"]) == 2


def test_build_slot_heatmap_renders_html(tmp_path: Path) -> None:
    month = "2026-03"
    agg_root = tmp_path / "aggregated"
    (agg_root / month).mkdir(parents=True)
    _hourly_rows().to_csv(
        agg_root / month / "15_slot_hourly.csv", index=False, encoding="utf-8-sig"
    )
    output = tmp_path / "slot_heatmap.html"
    build_slot_heatmap(
        months=[month],
        aggregated_root=agg_root,
        templates_dir=REPO_ROOT / "templates",
        output_path=output,
        classification_path=REPO_ROOT / "config" / "dept_classification.csv",
        theme_css="",
        common_js="",
    )
    html = output.read_text(encoding="utf-8")
    assert "外来枠×時間帯ヒートマップ" in html
    assert '"再診枠A"' in html
    assert '"追加枠"' in html
    assert '"DEPT_U"' in html
    assert "{{ dataset_json" not in html
