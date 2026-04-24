"""src/core/grading.py の単体テスト。"""
from src.core.grading import achievement_pct, grade_from_achievement


def test_forward_grades() -> None:
    assert grade_from_achievement(120) == "S"
    assert grade_from_achievement(110) == "S"
    assert grade_from_achievement(100) == "A"
    assert grade_from_achievement(95) == "B"
    assert grade_from_achievement(80) == "C"
    assert grade_from_achievement(50) == "D"


def test_inverse_grades() -> None:
    assert grade_from_achievement(80, inverse=True) == "S"
    assert grade_from_achievement(95, inverse=True) == "A"
    assert grade_from_achievement(105, inverse=True) == "B"
    assert grade_from_achievement(120, inverse=True) == "C"
    assert grade_from_achievement(150, inverse=True) == "D"


def test_achievement_zero_target() -> None:
    assert achievement_pct(100, 0) == 0.0
    assert achievement_pct(50, 100) == 50.0
