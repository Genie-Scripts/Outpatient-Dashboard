"""src/aggregate.py のヒートマップ・薬再診スコア集計ロジックの単体テスト。"""
from __future__ import annotations

import pandas as pd

from src.aggregate import (
    DRUG_REVISIT_MIN_RECORDS,
    _agg_doctor_hourly,
    _agg_drug_revisit_score,
    _agg_hourly_load,
    _preprocess,
    _valid_time_mask,
)


def _base_row(
    date: str = "2026-03-02",  # 月曜
    uketsuke: str = "09:00:00",
    kaishi: str = "09:05:00",
    shuryo: str = "09:12:00",
    shinsatsu_jikan: float = 7.0,
    shinsatsu_machi: float = 5.0,
    dept: str = "内科",
    doctor: str = "DR_U001",
    slot: str = "再診枠",
    kubun: str = "再診",
    shokai: str = "紹介状無し",
) -> dict:
    return {
        "予約日": date,
        "受付時刻": uketsuke,
        "開始時刻": kaishi,
        "終了時刻": shuryo,
        "会計終了時刻": "",
        "診察待時間": shinsatsu_machi,
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


def test_valid_time_mask_excludes_abnormal() -> None:
    df = pd.DataFrame([
        _base_row(kaishi="09:00:00", shuryo="09:10:00", shinsatsu_jikan=10),
        _base_row(kaishi="00:00:01", shuryo="00:00:10", shinsatsu_jikan=0.1),  # 異常
        _base_row(kaishi="21:00:00", shuryo="21:10:00", shinsatsu_jikan=10),  # 診療時間外
        _base_row(kaishi="10:00:00", shuryo="09:50:00", shinsatsu_jikan=10),  # 逆転
    ])
    proc = _preprocess(df)
    mask = _valid_time_mask(proc)
    assert mask.tolist() == [True, False, False, False]


def test_agg_hourly_load_arrivals_and_concurrent() -> None:
    rows = [
        _base_row(date="2026-03-02", kaishi="09:00:00", shuryo="09:10:00"),
        _base_row(date="2026-03-02", kaishi="09:05:00", shuryo="09:25:00"),
        _base_row(date="2026-03-09", kaishi="09:15:00", shuryo="09:30:00"),
    ]
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_hourly_load(proc)

    naika_monday_0900 = out[
        (out["診療科名"] == "内科") & (out["曜日"] == 0) & (out["bin_idx"] == 2)
    ]
    assert not naika_monday_0900.empty
    row = naika_monday_0900.iloc[0]
    assert row["bin_label"] == "09:00"
    assert row["到着件数_日平均"] > 0
    assert row["同時並行_最大"] >= 2
    assert row["該当日数"] == 2


def test_agg_drug_revisit_score_requires_min_records() -> None:
    rows = []
    for i in range(DRUG_REVISIT_MIN_RECORDS):
        rows.append(_base_row(
            date=f"2026-03-{(i % 20) + 1:02d}",
            shinsatsu_jikan=3.0,
            shokai="紹介状無し",
            slot="薬処方枠",
            doctor="DR_U001",
        ))
    for i in range(3):
        rows.append(_base_row(
            date=f"2026-03-{(i % 20) + 1:02d}",
            shinsatsu_jikan=10.0,
            slot="精査枠",
            doctor="DR_U002",
        ))
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_drug_revisit_score(proc)

    scored = out[out["スコア"].notna()]
    low_sample = out[out["再診件数"] < DRUG_REVISIT_MIN_RECORDS]
    assert len(scored) >= 1
    assert low_sample["スコア"].isna().all()

    drug_row = out[out["予約名称"] == "薬処方枠"].iloc[0]
    assert drug_row["短時間再診比率"] == 1.0
    assert drug_row["紹介状なし再診比率"] == 1.0
    assert drug_row["診察時間中央値_再診"] == 3.0


def test_agg_doctor_hourly_frequency_and_count() -> None:
    # 月曜: 4日 (3/2, 3/9, 3/16, 3/23)。DR_U001が毎週09:00bin、DR_U002が2/4日のみ09:30bin
    mondays = ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    rows = []
    for d in mondays:
        rows.append(_base_row(date=d, kaishi="09:00:00", shuryo="09:15:00", doctor="DR_U001"))
    rows.append(_base_row(date="2026-03-02", kaishi="09:30:00", shuryo="09:45:00", doctor="DR_U002"))
    rows.append(_base_row(date="2026-03-16", kaishi="09:30:00", shuryo="09:45:00", doctor="DR_U002"))
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_doctor_hourly(proc)

    zentai = out[out["区分"] == "全体"]
    u001_0900 = zentai[
        (zentai["予約担当者匿名ID"] == "DR_U001")
        & (zentai["曜日"] == 0)
        & (zentai["bin_idx"] == 2)
    ]
    assert not u001_0900.empty
    r = u001_0900.iloc[0]
    assert r["bin_label"] == "09:00"
    assert r["出勤日数"] == 4
    assert r["該当日数"] == 4
    assert r["出勤頻度率"] == 1.0
    assert r["件数合計"] == 4
    # 09:00-09:15 は 09:00bin 内に完全に収まる → 4日 × 15分 = 60分
    assert r["実診察分数"] == 60.0
    # 日平均: 4件/4日=1.0件, 60分/4日=15分
    assert r["件数_日平均"] == 1.0
    assert r["実診察分数_日平均"] == 15.0

    u002_0930 = zentai[
        (zentai["予約担当者匿名ID"] == "DR_U002")
        & (zentai["曜日"] == 0)
        & (zentai["bin_idx"] == 3)
    ]
    r = u002_0930.iloc[0]
    assert r["出勤日数"] == 2
    assert r["該当日数"] == 4
    assert r["出勤頻度率"] == 0.5
    assert r["件数合計"] == 2
    # 09:30-09:45 は 09:30bin 内に完全に収まる → 2日 × 15分 = 30分
    assert r["実診察分数"] == 30.0
    assert r["件数_日平均"] == 0.5
    assert r["実診察分数_日平均"] == 7.5


def test_agg_doctor_hourly_category_split() -> None:
    # 再診3件（うち1件は薬再診=3分）と初診2件を同ビンに投入
    rows = [
        _base_row(date="2026-03-02", kaishi="09:00:00", shuryo="09:10:00",
                  shinsatsu_jikan=10.0, kubun="再診"),
        _base_row(date="2026-03-09", kaishi="09:00:00", shuryo="09:10:00",
                  shinsatsu_jikan=8.0, kubun="再診"),
        _base_row(date="2026-03-16", kaishi="09:00:00", shuryo="09:05:00",
                  shinsatsu_jikan=3.0, kubun="再診"),  # 薬再診
        _base_row(date="2026-03-02", kaishi="09:00:00", shuryo="09:20:00",
                  shinsatsu_jikan=20.0, kubun="初診"),
        _base_row(date="2026-03-09", kaishi="09:00:00", shuryo="09:25:00",
                  shinsatsu_jikan=25.0, kubun="初診"),
    ]
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_doctor_hourly(proc)

    def pick(cat: str) -> pd.Series:
        sub = out[(out["区分"] == cat) & (out["曜日"] == 0) & (out["bin_idx"] == 2)]
        return sub.iloc[0] if not sub.empty else None

    zentai = pick("全体")
    assert zentai["件数合計"] == 5
    assert zentai["出勤日数"] == 3

    sho = pick("初診")
    assert sho["件数合計"] == 2
    assert sho["出勤日数"] == 2

    sai = pick("再診")
    assert sai["件数合計"] == 3
    assert sai["出勤日数"] == 3

    drug = pick("薬再診")
    assert drug["件数合計"] == 1
    assert drug["出勤日数"] == 1

    # 薬再診は再診の部分集合
    assert drug["件数合計"] <= sai["件数合計"]


def test_agg_doctor_hourly_duration_across_bins() -> None:
    # 09:20-09:50 の1回の診察は 09:00bin (09:20-09:30=10分) と 09:30bin (09:30-09:50=20分) に分割
    rows = [_base_row(date="2026-03-02", kaishi="09:20:00", shuryo="09:50:00", doctor="DR_U001")]
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_doctor_hourly(proc)
    zentai = out[out["区分"] == "全体"]
    b0900 = zentai[(zentai["bin_idx"] == 2)].iloc[0]
    b0930 = zentai[(zentai["bin_idx"] == 3)].iloc[0]
    assert b0900["実診察分数"] == 10.0
    assert b0930["実診察分数"] == 20.0
    assert b0900["件数合計"] == 1
    assert b0930["件数合計"] == 1


def test_agg_doctor_hourly_empty_when_no_valid() -> None:
    rows = [_base_row(kaishi="00:00:01", shuryo="00:00:10", shinsatsu_jikan=0.1)]
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_doctor_hourly(proc)
    assert out.empty
    assert list(out.columns) == [
        "診療科名", "予約担当者匿名ID", "区分", "曜日", "bin_idx", "bin_label",
        "出勤日数", "該当日数", "出勤頻度率",
        "件数合計", "実診察分数", "件数_日平均", "実診察分数_日平均",
    ]


def test_agg_drug_revisit_score_empty_when_no_sai() -> None:
    rows = [
        _base_row(kubun="初診"),
        _base_row(kubun="初診", doctor="DR_U002"),
    ]
    proc = _preprocess(pd.DataFrame(rows))
    out = _agg_drug_revisit_score(proc)
    assert out.empty
