"""予約名称匿名化の単体テスト。"""
from pathlib import Path

import pandas as pd

from src.anonymize import _anonymize_slot_names, _load_slot_key, SLOT_KEY_COLUMNS


def _make_df(names: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"予約名称": names, "診療科名": ["A"] * len(names)})


def test_new_slot_names_get_sl_ids(tmp_path: Path) -> None:
    slot_key = tmp_path / "slot_key.csv"
    df = _make_df(["初診枠A", "再診枠B"])

    result = _anonymize_slot_names(df, slot_key, "2026-04-24")

    assert result["予約名称"].tolist() == ["SL_0001", "SL_0002"]
    assert slot_key.exists()
    saved = pd.read_csv(slot_key, encoding="utf-8-sig")
    assert len(saved) == 2


def test_existing_mapping_is_preserved(tmp_path: Path) -> None:
    slot_key = tmp_path / "slot_key.csv"
    pd.DataFrame([
        {"予約名称": "初診枠A", "匿名ID": "SL_0001", "初回登録日": "2026-01-01"},
    ]).to_csv(slot_key, index=False, encoding="utf-8-sig")

    df = _make_df(["初診枠A", "新規枠C"])
    result = _anonymize_slot_names(df, slot_key, "2026-04-24")

    assert result[result["予約名称"] == "SL_0001"].shape[0] == 1
    assert result[result["予約名称"] == "SL_0002"].shape[0] == 1
    saved = pd.read_csv(slot_key, encoding="utf-8-sig")
    assert len(saved) == 2


def test_no_slot_column_is_noop(tmp_path: Path) -> None:
    slot_key = tmp_path / "slot_key.csv"
    df = pd.DataFrame({"診療科名": ["A", "B"]})

    result = _anonymize_slot_names(df, slot_key, "2026-04-24")

    assert list(result.columns) == ["診療科名"]
    assert not slot_key.exists()


def test_serial_increments_from_existing_max(tmp_path: Path) -> None:
    slot_key = tmp_path / "slot_key.csv"
    pd.DataFrame([
        {"予約名称": "既存枠", "匿名ID": "SL_0099", "初回登録日": "2026-01-01"},
    ]).to_csv(slot_key, index=False, encoding="utf-8-sig")

    df = _make_df(["新規枠"])
    result = _anonymize_slot_names(df, slot_key, "2026-04-24")

    assert result["予約名称"].iloc[0] == "SL_0100"
