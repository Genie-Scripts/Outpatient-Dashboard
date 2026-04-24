"""LM Studio クライアント（OpenAI互換API）。

config/llm_config.yaml から接続設定を読み込み、ハイライト候補から
見出し（HEAD）+ 本文（BODY）の自然文を生成する。
接続失敗時は定型文フォールバック。
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from src.core.highlights import HighlightCandidate

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    endpoint: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    system_prompt: str


class LLMClient:
    """LM Studio OpenAI互換APIでハイライト文章を生成するクライアント。"""

    def __init__(self, config_path: Path, enabled: bool = True) -> None:
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.config = LLMConfig(
            endpoint=raw["endpoint"],
            model=raw.get("model", "local-model"),
            temperature=float(raw.get("temperature", 0.3)),
            max_tokens=int(raw.get("max_tokens", 500)),
            timeout=int(raw.get("timeout", 120)),
            system_prompt=raw.get("system_prompt", ""),
        )
        self.enabled = enabled

    def generate_highlights(
        self, candidates: dict[str, HighlightCandidate | None]
    ) -> dict[str, dict[str, Any] | None]:
        """ハイライト候補から HEAD/BODY 辞書を生成する。

        Args:
            candidates: extract_highlights() の戻り値

        Returns:
            {"best": {head, body, raw}, "declining": ..., "worst": ...}
        """
        if not self.enabled:
            logger.info("LLM無効モード: フォールバック定型文で生成")
            return self._fallback(candidates)

        prompt = self._build_prompt(candidates)
        try:
            text = self._call_llm(prompt)
            return self._parse_response(text, candidates)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
            logger.warning("LLM呼び出し失敗 → フォールバック: %s", e)
            return self._fallback(candidates)

    def _call_llm(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.config.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        logger.info("LLM呼び出し: %s", self.config.endpoint)
        with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]

    @staticmethod
    def _build_prompt(candidates: dict[str, HighlightCandidate | None]) -> str:
        lines = [
            "以下の3つのハイライトについて、経営層に報告する文章を作ってください。",
            "見出し（HEAD:）と本文（BODY:）を分けて出力してください。",
            "",
        ]
        best = candidates.get("best")
        if best:
            lines += [
                "【1. 好事例】",
                f"診療科: {best.name}",
                f"初診件数: 前月 {best.sho_prev}件 → 最新月 {best.sho_latest}件 "
                f"(前月比 {best.pct_change:+.1f}%)",
                f"目標 {best.target}件に対し達成率 {best.achievement:.0f}%",
                "HEAD: （15字以内の見出し）",
                "BODY: （60字程度の本文。数値は必ず上記から引用し、改変しないこと）",
                "",
            ]
        declining = candidates.get("declining")
        if declining:
            lines += [
                "【2. 連続悪化】",
                f"診療科: {declining.name}",
                f"初診件数が3ヶ月連続で減少傾向。最新月 {declining.sho_latest}件"
                f"（目標 {declining.target}件）",
                "HEAD: （15字以内の見出し）",
                "BODY: （60字程度の本文。早期対応の必要性を示唆）",
                "",
            ]
        worst = candidates.get("worst")
        if worst:
            lines += [
                "【3. 目標未達】",
                f"診療科: {worst.name}",
                f"初診達成率 {worst.achievement:.0f}%（最新月 {worst.sho_latest}件 / "
                f"目標 {worst.target}件）",
                "HEAD: （15字以内の見出し）",
                "BODY: （60字程度の本文。個別ヒアリングや要因分析の必要性）",
                "",
            ]
        lines += [
            "出力形式の例:",
            "1. HEAD: 初診件数が大幅増加",
            "   BODY: ○○科の初診件数が前月比+15%と好調。取り組みの要因分析と横展開を検討。",
        ]
        return "\n".join(lines)

    def _parse_response(
        self,
        text: str,
        candidates: dict[str, HighlightCandidate | None],
    ) -> dict[str, dict[str, Any] | None]:
        results: dict[str, dict[str, Any] | None] = {"best": None, "declining": None, "worst": None}
        keys = ["best", "declining", "worst"]
        sections = re.split(r"\n\s*(?:【?[1-3]|\d+[\.\)\uff0e])", text)

        for i, key in enumerate(keys):
            if i + 1 < len(sections) and candidates.get(key):
                sec = sections[i + 1]
                head_m = re.search(r"HEAD[:：]\s*(.+?)(?:\n|$)", sec)
                body_m = re.search(r"BODY[:：]\s*(.+?)(?:\n\n|\nHEAD|$)", sec, re.DOTALL)
                if head_m and body_m:
                    results[key] = {
                        "head": head_m.group(1).strip(),
                        "body": body_m.group(1).strip().replace("\n", " "),
                        "raw": asdict(candidates[key]),
                    }

        fb = self._fallback(candidates)
        for key in keys:
            if results[key] is None and candidates.get(key):
                results[key] = fb[key]
        return results

    @staticmethod
    def _fallback(
        candidates: dict[str, HighlightCandidate | None]
    ) -> dict[str, dict[str, Any] | None]:
        results: dict[str, dict[str, Any] | None] = {"best": None, "declining": None, "worst": None}
        best = candidates.get("best")
        if best:
            results["best"] = {
                "head": "今月の好事例（初診件数）",
                "body": (
                    f"{best.name}の初診件数が前月比 {best.pct_change:+.1f}%。"
                    f"最新月 {best.sho_latest}件（前月 {best.sho_prev}件）。"
                    "要因検証と横展開を検討。"
                ),
                "raw": asdict(best),
            }
        declining = candidates.get("declining")
        if declining:
            results["declining"] = {
                "head": "連続悪化傾向",
                "body": (
                    f"{declining.name}の初診件数が3ヶ月連続で減少傾向。"
                    f"最新月 {declining.sho_latest}件。早期の要因分析が必要。"
                ),
                "raw": asdict(declining),
            }
        worst = candidates.get("worst")
        if worst:
            results["worst"] = {
                "head": "目標達成率 最下位",
                "body": (
                    f"{worst.name}の初診達成率は {worst.achievement:.0f}%。"
                    f"最新月 {worst.sho_latest}件／目標 {worst.target}件。個別ヒアリング対象。"
                ),
                "raw": asdict(worst),
            }
        return results
