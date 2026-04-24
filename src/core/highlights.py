"""ハイライト候補抽出。

月次ダッシュボード画面1の「好事例／連続悪化／目標未達」カード用に
数値データから候補となる診療科を抽出する（LLMに渡す前の準備）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HighlightCandidate:
    """ハイライト候補の生データ（LLM入力用）。"""

    name: str
    sho_latest: int
    sho_prev: int
    pct_change: float
    achievement: float
    target: int


def extract_highlights(depts_data: list[dict[str, Any]]) -> dict[str, HighlightCandidate | None]:
    """診療科別データから best / declining / worst の3候補を抽出する。

    Args:
        depts_data: 各要素が以下のキーを持つ診療科別データ
            - name: 診療科名
            - sho_m: 月別初診件数リスト
            - sho_target: 初診目標

    Returns:
        {"best": ..., "declining": ..., "worst": ...} の辞書。
        該当なしの項目は None。
    """
    candidates: list[HighlightCandidate] = []
    for d in depts_data:
        target = d.get("sho_target", 0)
        sho_m = d.get("sho_m", [])
        if target < 20 or len(sho_m) < 2:
            continue
        sho_latest = sho_m[-1]
        sho_prev = sho_m[-2]
        if sho_prev == 0:
            continue
        pct_change = (sho_latest / sho_prev - 1) * 100
        ach = (sho_latest / target) * 100 if target > 0 else 0
        candidates.append(HighlightCandidate(
            name=d["name"],
            sho_latest=sho_latest,
            sho_prev=sho_prev,
            pct_change=round(pct_change, 1),
            achievement=round(ach, 0),
            target=target,
        ))

    best = _pick_best(candidates)
    worst = _pick_worst(candidates)
    declining = _pick_declining(depts_data)

    return {"best": best, "declining": declining, "worst": worst}


def _pick_best(candidates: list[HighlightCandidate]) -> HighlightCandidate | None:
    """前月比プラスの中で増加率最大を返す。"""
    positives = [c for c in candidates if c.pct_change > 0]
    return max(positives, key=lambda x: x.pct_change) if positives else None


def _pick_worst(candidates: list[HighlightCandidate]) -> HighlightCandidate | None:
    """達成率が最も低い候補を返す。"""
    return min(candidates, key=lambda x: x.achievement) if candidates else None


def _pick_declining(depts_data: list[dict[str, Any]]) -> HighlightCandidate | None:
    """3ヶ月連続減少の診療科を検出して返す。"""
    for d in depts_data:
        target = d.get("sho_target", 0)
        m = d.get("sho_m", [])
        if target < 20 or len(m) < 4:
            continue
        if m[-2] < m[-3] < m[-4]:
            return HighlightCandidate(
                name=d["name"],
                sho_latest=m[-1],
                sho_prev=m[-2],
                pct_change=0.0,
                achievement=round(m[-1] / target * 100, 0) if target > 0 else 0,
                target=target,
            )
    return None
