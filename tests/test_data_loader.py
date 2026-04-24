"""src/core/data_loader.py の単体テスト。"""
from pathlib import Path

import pandas as pd
import pytest

from src.core.data_loader import _FILES, load_aggregated_data, load_last_n_months


def _write_dummy(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for fname in _FILES.values():
        pd.DataFrame([{"診療科名": "泌尿器科", "件数": 1}]).to_csv(
            base / fname, index=False, encoding="utf-8-sig"
        )


def test_loads_all_twelve_files(tmp_path: Path) -> None:
    month = "2026-04"
    _write_dummy(tmp_path / month)

    data = load_aggregated_data(tmp_path, month)

    assert data.month == month
    for key in _FILES.keys():
        frame = getattr(data, key)
        assert not frame.empty
        assert "診療科名" in frame.columns


def test_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_aggregated_data(tmp_path, "2000-01")


def test_load_last_n_months_returns_up_to_n(tmp_path: Path) -> None:
    for m in ["2026-01", "2026-02", "2026-03", "2026-04"]:
        _write_dummy(tmp_path / m)

    result = load_last_n_months(tmp_path, "2026-04", n=3)
    assert result == ["2026-02", "2026-03", "2026-04"]


def test_load_last_n_months_fewer_than_n(tmp_path: Path) -> None:
    _write_dummy(tmp_path / "2026-04")
    result = load_last_n_months(tmp_path, "2026-04", n=6)
    assert result == ["2026-04"]


def test_load_last_n_months_excludes_future(tmp_path: Path) -> None:
    for m in ["2026-03", "2026-04", "2026-05"]:
        _write_dummy(tmp_path / m)

    result = load_last_n_months(tmp_path, "2026-04", n=6)
    assert "2026-05" not in result
    assert result == ["2026-03", "2026-04"]
