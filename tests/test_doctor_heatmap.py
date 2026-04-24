"""src/dashboards/doctor_heatmap.py の生成ロジック単体テスト。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.dashboards.doctor_heatmap import (
    _build_dataset,
    _build_dept_series,
    build_doctor_heatmap,
)
from src.core.classify import DeptClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent


def _hourly_row(
    dept: str, doctor: str, cat: str, wd: int, bi: int,
    freq: float, count: float, duration: float,
    shukkin: int = 1, days: int = 4,
) -> dict:
    return {
        "診療科名": dept,
        "予約担当者匿名ID": doctor,
        "区分": cat,
        "曜日": wd,
        "bin_idx": bi,
        "bin_label": f"{8 + bi // 2:02d}:{(bi % 2) * 30:02d}",
        "出勤日数": shukkin,
        "該当日数": days,
        "出勤頻度率": freq,
        "件数合計": count,
        "実診察分数": duration,
        "件数_日平均": round(count / days, 2) if days else 0.0,
        "実診察分数_日平均": round(duration / days, 1) if days else 0.0,
    }


def _hourly_rows() -> pd.DataFrame:
    rows = []
    # 泌尿器科 DR_U001 月曜09:00: 全体12件/初診4件/再診8件/薬再診2件
    for cat, count, dur in [("全体", 12, 180.0), ("初診", 4, 80.0), ("再診", 8, 100.0), ("薬再診", 2, 10.0)]:
        rows.append(_hourly_row("泌尿器科", "DR_U001", cat, 0, 2, 1.0, count, dur, shukkin=4))
    # 泌尿器科 DR_U002 月曜09:30: 全体3件
    for cat, count, dur in [("全体", 3, 45.0), ("再診", 3, 45.0), ("薬再診", 1, 5.0)]:
        rows.append(_hourly_row("泌尿器科", "DR_U002", cat, 0, 3, 0.5, count, dur, shukkin=2))
    # 眼科 DR_E001 水曜10:00
    for cat, count, dur in [("全体", 8, 110.0), ("初診", 3, 50.0), ("再診", 5, 60.0)]:
        rows.append(_hourly_row("眼科", "DR_E001", cat, 2, 4, 0.75, count, dur, shukkin=3))
    return pd.DataFrame(rows)


def test_build_dept_series_orders_by_total_desc() -> None:
    df = _hourly_rows()
    rows = _build_dept_series(df[df["診療科名"] == "泌尿器科"])
    assert [r["id"] for r in rows] == ["DR_U001", "DR_U002"]
    assert rows[0]["total"] == 12
    zentai = rows[0]["categories"]["全体"]
    assert zentai["frequency"][0][2] == 1.0
    assert zentai["duration"][0][2] == 180.0
    assert zentai["count_per_day"][0][2] == 3.0
    assert zentai["duration_per_day"][0][2] == 45.0
    sho = rows[0]["categories"]["初診"]
    assert sho["count"][0][2] == 4.0
    sai = rows[0]["categories"]["再診"]
    assert sai["count"][0][2] == 8.0
    drug = rows[0]["categories"]["薬再診"]
    assert drug["count"][0][2] == 2.0
    # 医師2の 09:30 ビン
    assert rows[1]["categories"]["全体"]["frequency"][0][3] == 0.5
    assert rows[1]["categories"]["全体"]["count"][0][3] == 3.0
    assert rows[1]["categories"]["薬再診"]["count"][0][3] == 1.0


def test_build_dataset_keys_and_weekday_day_count() -> None:
    df = _hourly_rows()
    classifier = DeptClassifier(REPO_ROOT / "config" / "dept_classification.csv")
    ds = _build_dataset(df, classifier)
    assert "DEPT_U" in ds
    assert "DEPT_E" in ds
    assert ds["DEPT_U"]["label"] == "泌尿器科"
    assert ds["DEPT_U"]["weekday_day_count"][0] == 4
    assert len(ds["DEPT_U"]["doctors"]) == 2


def test_build_doctor_heatmap_renders_html(tmp_path: Path) -> None:
    month = "2026-03"
    agg_root = tmp_path / "aggregated"
    (agg_root / month).mkdir(parents=True)
    _hourly_rows().to_csv(
        agg_root / month / "14_doctor_hourly.csv", index=False, encoding="utf-8-sig"
    )
    output = tmp_path / "doctor_heatmap.html"
    build_doctor_heatmap(
        months=[month],
        aggregated_root=agg_root,
        templates_dir=REPO_ROOT / "templates",
        output_path=output,
        classification_path=REPO_ROOT / "config" / "dept_classification.csv",
        theme_css="",
        common_js="",
    )
    html = output.read_text(encoding="utf-8")
    assert "医師×時間帯ヒートマップ" in html
    assert '"DR_U001"' in html  # JSON化された医師IDが含まれる
    assert '"DEPT_U"' in html
    assert '"泌尿器科"' in html
    assert "{{ dataset_json" not in html  # プレースホルダが残っていない
