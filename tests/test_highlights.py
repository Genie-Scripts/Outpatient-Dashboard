"""src/core/highlights.py の単体テスト。"""
from src.core.highlights import extract_highlights


def _make(name: str, sho_m: list[int], target: int) -> dict:
    return {"name": name, "sho_m": sho_m, "sho_target": target}


def test_best_picks_max_growth() -> None:
    data = [
        _make("A", [50, 80], 70),
        _make("B", [100, 105], 100),
        _make("C", [40, 30], 50),  # マイナスは除外
    ]
    result = extract_highlights(data)
    assert result["best"] is not None
    assert result["best"].name == "A"
    assert result["best"].pct_change == 60.0


def test_worst_picks_lowest_achievement() -> None:
    data = [
        _make("A", [50, 80], 70),
        _make("B", [100, 105], 100),
        _make("C", [40, 30], 50),
    ]
    result = extract_highlights(data)
    assert result["worst"] is not None
    assert result["worst"].name == "C"


def test_declining_detects_three_month_drop() -> None:
    data = [
        _make("DropDept", [60, 50, 40, 30, 25], 60),
    ]
    result = extract_highlights(data)
    assert result["declining"] is not None
    assert result["declining"].name == "DropDept"


def test_small_target_excluded() -> None:
    data = [_make("Small", [5, 10], 10)]  # target<20 は除外
    result = extract_highlights(data)
    assert result["best"] is None
    assert result["worst"] is None


def test_empty_input() -> None:
    result = extract_highlights([])
    assert result == {"best": None, "declining": None, "worst": None}
