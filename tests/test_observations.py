"""src/core/observations.py の単体テスト（LLM 非依存）。"""
from __future__ import annotations

import pytest

from src.core.observations import (
    drug_revisit_facts_dict,
    drug_revisit_fallback_comment,
    extract_drug_revisit_observation,
)


def _make_section(
    name: str = "がんゲノム",
    rows: list[dict] | None = None,
    sai_total: int = 100,
    short_ratio: float = 30.0,
    no_shokai_ratio: float = 40.0,
    scoreable_count: int = 5,
    high_score_count: int = 2,
) -> dict:
    return {
        "name": name,
        "code": "ON",
        "type_label": "内科",
        "anchor": "drev-ON",
        "sai_total": sai_total,
        "short_ratio": short_ratio,
        "no_shokai_ratio": no_shokai_ratio,
        "scoreable_count": scoreable_count,
        "high_score_count": high_score_count,
        "rows": rows or [],
    }


def _row(medic: str, score: float | None, short_ratio: float = 50.0, sai: int = 30) -> dict:
    return {
        "medic": medic,
        "slot": "SL_0001",
        "month": "2026-04",
        "sai": sai,
        "short_ratio": short_ratio,
        "no_shokai_ratio": 60.0,
        "median_exam": 3.0,
        "score": score,
        "scoreable": score is not None,
    }


def test_extract_basic_fields() -> None:
    section = _make_section(
        rows=[_row("DR_ON001", 75.0), _row("DR_ON002", 60.5), _row("DR_ON003", 50.0)],
    )
    obs = extract_drug_revisit_observation(section)
    assert obs.dept_name == "がんゲノム"
    assert obs.sai_total == 100
    assert obs.short_ratio == 30.0
    assert obs.scoreable_count == 5
    assert obs.high_score_count == 2
    assert len(obs.top_rows) == 3
    assert obs.top_rows[0]["medic"] == "DR_ON001"
    assert obs.top_rows[0]["score"] == 75.0


def test_extract_caps_top_rows_at_three() -> None:
    rows = [_row(f"DR_ON{i:03d}", 90.0 - i) for i in range(1, 8)]
    obs = extract_drug_revisit_observation(_make_section(rows=rows))
    assert len(obs.top_rows) == 3
    assert [r["medic"] for r in obs.top_rows] == ["DR_ON001", "DR_ON002", "DR_ON003"]


def test_extract_rejects_non_anonymized_id() -> None:
    bad_section = _make_section(rows=[_row("山田太郎", 75.0)])
    with pytest.raises(ValueError, match="匿名IDフォーマット違反"):
        extract_drug_revisit_observation(bad_section)


def test_extract_rejects_master_key_style_id() -> None:
    bad_section = _make_section(rows=[_row("DOC_001", 75.0)])
    with pytest.raises(ValueError):
        extract_drug_revisit_observation(bad_section)


def test_fallback_comment_no_scoreable() -> None:
    obs = extract_drug_revisit_observation(
        _make_section(rows=[], scoreable_count=0, high_score_count=0)
    )
    msg = drug_revisit_fallback_comment(obs)
    assert "採点対象" in msg
    assert "がんゲノム" in msg


def test_fallback_comment_zero_high_score() -> None:
    obs = extract_drug_revisit_observation(
        _make_section(
            rows=[_row("DR_ON001", 40.0)],
            scoreable_count=3,
            high_score_count=0,
        )
    )
    msg = drug_revisit_fallback_comment(obs)
    assert "0件" in msg or "採点済み" in msg


def test_fallback_comment_uses_top_row_when_scored() -> None:
    obs = extract_drug_revisit_observation(
        _make_section(
            rows=[_row("DR_ON001", 78.5, short_ratio=82.0, sai=145)],
            high_score_count=1,
        )
    )
    msg = drug_revisit_fallback_comment(obs)
    assert "DR_ON001" in msg
    assert "78.5" in msg
    assert "82.0" in msg
    assert "145" in msg


def test_facts_dict_is_jsonable() -> None:
    import json
    obs = extract_drug_revisit_observation(
        _make_section(rows=[_row("DR_ON001", 75.0)])
    )
    facts = drug_revisit_facts_dict(obs)
    s = json.dumps(facts, sort_keys=True, ensure_ascii=False)
    again = json.dumps(json.loads(s), sort_keys=True, ensure_ascii=False)
    assert s == again
