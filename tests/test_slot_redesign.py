"""src/dashboards/slot_redesign.py のスロット分類ロジック単体テスト。"""
import pandas as pd

from src.dashboards.slot_redesign import _build_slot_table


def _row(name: str, kubun: str, shokai: str, count: int) -> dict:
    return {
        "診療科名": "泌尿器科",
        "予約名称": name,
        "初再診区分": kubun,
        "紹介状有無": shokai,
        "件数": count,
    }


def test_rare_slot_flagged() -> None:
    df = pd.DataFrame([
        _row("稀用枠", "初診", "紹介状無し", 2),
        _row("主力枠", "再診", "紹介状無し", 100),
    ])
    rows = _build_slot_table(df, "泌尿器科")
    rare = next(r for r in rows if r["name"] == "稀用枠")
    assert "稀用" in rare["flags"]
    main = next(r for r in rows if r["name"] == "主力枠")
    assert "稀用" not in main["flags"]


def test_naming_discrepancy_sho_but_sai_majority() -> None:
    df = pd.DataFrame([
        _row("初診枠A", "初診", "紹介状無し", 10),
        _row("初診枠A", "再診", "紹介状無し", 40),  # 再診80%
    ])
    rows = _build_slot_table(df, "泌尿器科")
    assert len(rows) == 1
    assert any("初診名だが再診多用" in f for f in rows[0]["flags"])


def test_naming_discrepancy_shokai_but_none() -> None:
    df = pd.DataFrame([
        _row("紹介枠", "初診", "紹介状無し", 50),
        _row("紹介枠", "初診", "紹介状あり", 5),
    ])
    rows = _build_slot_table(df, "泌尿器科")
    assert any("紹介名だが紹介状無し多用" in f for f in rows[0]["flags"])


def test_a_slot_candidate_unnamed() -> None:
    df = pd.DataFrame([
        _row("通常枠", "初診", "紹介状あり", 20),
        _row("通常枠", "初診", "紹介状無し", 10),
    ])
    rows = _build_slot_table(df, "泌尿器科")
    assert any("A枠候補" in f for f in rows[0]["flags"])


def test_other_dept_returns_empty() -> None:
    df = pd.DataFrame([_row("枠X", "初診", "紹介状あり", 10)])
    rows = _build_slot_table(df, "眼科")
    assert rows == []
