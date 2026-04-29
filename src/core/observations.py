"""観察ファクト抽出層。

各ダッシュボードのセクションから「LLM へ渡す事実だけ」を純関数で取り出す。
LLM は計算しない／観察コメントは Python が組み立てた事実を 1〜2 文に翻訳するだけ、
という原則（CLAUDE.md「LLMに計算をさせない」）を守るための境界。

このモジュール自体は LLM に依存しない（pytest 可能）。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# 医師匿名ID の妥当性チェック。実名や master_key 由来の文字列が
# LLM プロンプトに混入していないかをガードする目的。
# 形式: DR_<診療科コード(1〜3 文字)><3 〜 4 桁連番> 例: DR_U001 / DR_RS2009
_ANON_ID_RE = re.compile(r"^DR_[A-Z]{1,3}\d{3,4}$")


def _assert_anon_id(value: str) -> None:
    if not _ANON_ID_RE.match(value):
        raise ValueError(f"匿名IDフォーマット違反: {value!r}")


@dataclass
class DrugRevisitObservation:
    """薬再診候補スコア セクションの観察ファクト。

    LLM へ渡す前の純粋な数値・短い文字列のみを保持する。
    医師実名・予約名称（実名）は含めない（匿名ID／予約名称匿名IDのみ）。
    """

    dept_name: str
    sai_total: int                 # 再診件数（科合計）
    short_ratio: float             # 短時間再診比率（％, 0-100）
    no_shokai_ratio: float         # 紹介状なし再診比率（％, 0-100）
    scoreable_count: int           # 採点済み医師×枠の数
    high_score_count: int          # スコア≥60 の数
    top_rows: list[dict[str, Any]] = field(default_factory=list)
    # ↑ 上位スコア行（最大3件）。各 dict は medic / slot / score / short_ratio / sai


def extract_drug_revisit_observation(section: dict[str, Any]) -> DrugRevisitObservation:
    """drug_revisit ダッシュボードの 1 科分セクションから観察ファクトを抽出する。

    Args:
        section: drug_revisit._build_dept_sections の戻り値の 1 要素。
            想定キー: name, sai_total, short_ratio, no_shokai_ratio,
                     scoreable_count, high_score_count, rows

    Returns:
        DrugRevisitObservation: LLM 入力／フォールバック文に使う事実集合。

    Raises:
        ValueError: rows に含まれる医師匿名ID が DR_ プレフィクス形式でない場合。
    """
    rows = section.get("rows") or []
    top_rows: list[dict[str, Any]] = []
    for r in rows[:3]:
        medic = str(r.get("medic", ""))
        _assert_anon_id(medic)
        top_rows.append({
            "medic": medic,
            "slot": str(r.get("slot", "")),
            "score": r.get("score"),
            "short_ratio": r.get("short_ratio"),
            "sai": r.get("sai"),
        })

    return DrugRevisitObservation(
        dept_name=str(section["name"]),
        sai_total=int(section.get("sai_total", 0)),
        short_ratio=float(section.get("short_ratio", 0.0)),
        no_shokai_ratio=float(section.get("no_shokai_ratio", 0.0)),
        scoreable_count=int(section.get("scoreable_count", 0)),
        high_score_count=int(section.get("high_score_count", 0)),
        top_rows=top_rows,
    )


def drug_revisit_fallback_comment(obs: DrugRevisitObservation) -> str:
    """LLM 不在時の決定的な観察コメント（数値ベースの定型文）。

    LLM が呼べない／無効化されている時に表示するため、
    obs の数値だけから 1 文を組み立てる（幻覚ゼロ）。
    """
    if obs.scoreable_count == 0:
        return (
            f"{obs.dept_name}は採点対象の医師×枠が無く、"
            f"逆紹介候補リスト化はスキップ。"
        )

    if obs.high_score_count == 0:
        return (
            f"{obs.dept_name}は採点済み {obs.scoreable_count} 件中スコア≥60が0件。"
            f"短時間再診比率 {obs.short_ratio:.1f}% / 紹介状なし {obs.no_shokai_ratio:.1f}%。"
        )

    top = obs.top_rows[0] if obs.top_rows else None
    if top and top.get("score") is not None:
        return (
            f"{obs.dept_name}はスコア≥60が {obs.high_score_count} 件"
            f"（採点済み {obs.scoreable_count} 件中）。"
            f"最上位 {top['medic']} × {top['slot']}：スコア{top['score']:.1f}・"
            f"短時間再診{top['short_ratio']:.1f}%・再診{top['sai']}件。"
        )
    return (
        f"{obs.dept_name}はスコア≥60が {obs.high_score_count} 件"
        f"（採点済み {obs.scoreable_count} 件中）。"
        f"短時間再診比率 {obs.short_ratio:.1f}% / 紹介状なし {obs.no_shokai_ratio:.1f}%。"
    )


def drug_revisit_facts_dict(obs: DrugRevisitObservation) -> dict[str, Any]:
    """LLM プロンプト／キャッシュキー用の dict 表現。

    dataclasses.asdict と等価だが、キャッシュキー安定化のため
    将来フィールド追加時に意識的にここを通すことを意図する。
    """
    return asdict(obs)
