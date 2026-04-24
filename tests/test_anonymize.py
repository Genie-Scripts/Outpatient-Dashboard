"""src/anonymize.py の単体テスト。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.anonymize import anonymize_monthly_data


@pytest.fixture
def dept_classification(tmp_path: Path) -> Path:
    path = tmp_path / "dept_classification.csv"
    pd.DataFrame([
        {"診療科名": "泌尿器科", "タイプ": "外科系", "診療科コード": "U",
         "表示順": 1, "評価対象": "TRUE", "備考": ""},
        {"診療科名": "皮膚科", "タイプ": "外科系", "診療科コード": "DM",
         "表示順": 2, "評価対象": "TRUE", "備考": ""},
    ]).to_csv(path, index=False, encoding="utf-8-sig")
    return path


@pytest.fixture
def master_key(tmp_path: Path) -> Path:
    path = tmp_path / "master_key.csv"
    pd.DataFrame(columns=["実名", "匿名ID", "診療科名", "初回登録日", "備考"]).to_csv(
        path, index=False, encoding="utf-8-sig"
    )
    return path


def _write_raw(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def test_new_registration_assigns_sequential_ids(
    tmp_path: Path, dept_classification: Path, master_key: Path
) -> None:
    raw = tmp_path / "raw.csv"
    _write_raw(raw, [
        {"予約担当者名": "山田太郎", "診療科名": "泌尿器科"},
        {"予約担当者名": "鈴木次郎", "診療科名": "泌尿器科"},
        {"予約担当者名": "佐藤花子", "診療科名": "皮膚科"},
    ])

    out = tmp_path / "anon.csv"
    result = anonymize_monthly_data(
        input_path=raw,
        output_path=out,
        master_key_path=master_key,
        dept_classification_path=dept_classification,
        today="2026-04-23",
    )

    assert result.total_rows == 3
    assert result.unique_names_total == 3
    assert len(result.newly_registered) == 3

    master_df = pd.read_csv(master_key, encoding="utf-8-sig")
    id_map = dict(zip(master_df["実名"], master_df["匿名ID"]))
    assert id_map["山田太郎"] == "DR_U001"
    assert id_map["鈴木次郎"] == "DR_U002"
    assert id_map["佐藤花子"] == "DR_DM001"

    anon_df = pd.read_csv(out, encoding="utf-8-sig")
    assert "予約担当者名" not in anon_df.columns
    assert "予約担当者匿名ID" in anon_df.columns
    assert set(anon_df["予約担当者匿名ID"]) == {"DR_U001", "DR_U002", "DR_DM001"}


def test_existing_master_key_is_preserved(
    tmp_path: Path, dept_classification: Path, master_key: Path
) -> None:
    pd.DataFrame([{
        "実名": "山田太郎", "匿名ID": "DR_U001", "診療科名": "泌尿器科",
        "初回登録日": "2025-01-01", "備考": "",
    }]).to_csv(master_key, index=False, encoding="utf-8-sig")

    raw = tmp_path / "raw.csv"
    _write_raw(raw, [
        {"予約担当者名": "山田太郎", "診療科名": "泌尿器科"},
        {"予約担当者名": "鈴木次郎", "診療科名": "泌尿器科"},
    ])

    out = tmp_path / "anon.csv"
    result = anonymize_monthly_data(
        input_path=raw,
        output_path=out,
        master_key_path=master_key,
        dept_classification_path=dept_classification,
        today="2026-04-23",
    )

    assert len(result.newly_registered) == 1
    assert result.newly_registered[0][0] == "鈴木次郎"
    assert result.newly_registered[0][1] == "DR_U002"


def test_missing_source_column_raises(
    tmp_path: Path, dept_classification: Path, master_key: Path
) -> None:
    raw = tmp_path / "raw.csv"
    _write_raw(raw, [{"他の列": "A", "診療科名": "泌尿器科"}])

    with pytest.raises(ValueError, match="予約担当者名"):
        anonymize_monthly_data(
            input_path=raw,
            output_path=tmp_path / "anon.csv",
            master_key_path=master_key,
            dept_classification_path=dept_classification,
        )
