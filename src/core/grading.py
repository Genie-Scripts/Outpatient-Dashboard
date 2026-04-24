"""目標達成率から S〜D 評価を計算する。

- 正方向指標（初診件数・紹介率など大きいほど良い）: S≥110%, A≥100%, B≥90%, C≥75%, D<75%
- 逆方向指標（薬のみ再診・午前集中度など小さいほど良い）: 閾値を反転
"""
from __future__ import annotations

Grade = str  # "S" | "A" | "B" | "C" | "D"


def grade_from_achievement(pct: float, inverse: bool = False) -> Grade:
    """達成率（%）から評価ランクを返す。

    Args:
        pct: 目標達成率（実績/目標 × 100）
        inverse: Trueなら逆方向指標として扱う（低いほど良い）

    Returns:
        "S" / "A" / "B" / "C" / "D" のいずれか
    """
    if inverse:
        if pct <= 90:
            return "S"
        if pct <= 100:
            return "A"
        if pct <= 110:
            return "B"
        if pct <= 125:
            return "C"
        return "D"

    if pct >= 110:
        return "S"
    if pct >= 100:
        return "A"
    if pct >= 90:
        return "B"
    if pct >= 75:
        return "C"
    return "D"


def achievement_pct(actual: float, target: float) -> float:
    """達成率（%）を返す。目標が0なら0。"""
    if target <= 0:
        return 0.0
    return actual / target * 100
