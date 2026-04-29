"""Ollama / OpenAI互換APIクライアント。

config/llm_config.yaml から接続設定を読み込み、ハイライト候補から
見出し（HEAD）+ 本文（BODY）の自然文を生成する。

Ollama が未起動の場合は `ollama serve` を自動起動し、最大 _STARTUP_TIMEOUT 秒待機する。
起動失敗・モデル未取得・API エラーの場合は定型文フォールバック。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from src.core.highlights import HighlightCandidate

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT: int = 30   # 秒: ollama serve 起動待ちの上限
_HEALTH_INTERVAL: int = 1    # 秒: ヘルスチェックのポーリング間隔


@dataclass
class LLMConfig:
    endpoint: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    system_prompt: str


class LLMClient:
    """Ollama / LM Studio OpenAI互換APIでハイライト文章を生成するクライアント。"""

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
        parsed = urlparse(self.config.endpoint)
        self._base_url = f"{parsed.scheme}://{parsed.netloc}"

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

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

        if not self._ensure_server():
            logger.warning("Ollama サーバに接続できません → フォールバック")
            return self._fallback(candidates)

        if not self._is_model_available():
            logger.warning(
                "モデル '%s' が未取得です。`ollama pull %s` を実行してください → フォールバック",
                self.config.model,
                self.config.model,
            )
            return self._fallback(candidates)

        prompt = self._build_prompt(candidates)
        try:
            text = self._call_llm(prompt)
            return self._parse_response(text, candidates)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
            logger.warning("LLM呼び出し失敗 → フォールバック: %s", e)
            return self._fallback(candidates)

    # ------------------------------------------------------------------ #
    # Ollama ライフサイクル管理
    # ------------------------------------------------------------------ #

    def _is_ollama_up(self) -> bool:
        """Ollama が HTTP レスポンスを返すか確認する。"""
        try:
            with urllib.request.urlopen(f"{self._base_url}/", timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _start_ollama(self) -> bool:
        """ollama serve をバックグラウンドで起動し、最大 _STARTUP_TIMEOUT 秒待機する。

        Returns:
            起動に成功した場合 True、失敗した場合 False。
        """
        logger.info("Ollama 未起動を検知 → `ollama serve` を自動起動します")
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning(
                "ollama コマンドが見つかりません。Ollama をインストールし PATH を通してください。"
            )
            return False

        for elapsed in range(_STARTUP_TIMEOUT):
            time.sleep(_HEALTH_INTERVAL)
            if self._is_ollama_up():
                logger.info("Ollama 起動完了（%d 秒）", elapsed + 1)
                return True

        logger.warning("Ollama の起動が %d 秒でタイムアウトしました", _STARTUP_TIMEOUT)
        return False

    def _ensure_server(self) -> bool:
        """起動済みなら即 True、未起動なら自動起動して True/False を返す。"""
        if self._is_ollama_up():
            return True
        return self._start_ollama()

    def _is_model_available(self) -> bool:
        """設定されたモデルが Ollama にプル済みか確認する。

        確認できない場合（API エラーなど）は楽観的に True を返す。

        Returns:
            利用可能と判断できる場合 True。
        """
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/api/tags", timeout=5
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning("モデル一覧の取得に失敗しました（楽観的続行）: %s", e)
            return True

        available = [m["name"] for m in data.get("models", [])]
        config_model = self.config.model
        base_name = config_model.split(":")[0]
        matched = any(
            m == config_model
            or m.startswith(base_name + ":")
            or m == base_name
            for m in available
        )
        if not matched:
            logger.debug("取得済みモデル一覧: %s", available)
        return matched

    # ------------------------------------------------------------------ #
    # LLM 呼び出し
    # ------------------------------------------------------------------ #

    def _call_llm(self, prompt: str) -> str:
        """OpenAI互換エンドポイントにリクエストを送り、応答テキストを返す。"""
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

    # ------------------------------------------------------------------ #
    # プロンプト生成 / レスポンス解析
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_prompt(candidates: dict[str, HighlightCandidate | None]) -> str:
        """ハイライト候補から LLM へ渡すプロンプトを組み立てる。"""
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
        """LLM の応答テキストから HEAD/BODY を抽出する。"""
        results: dict[str, dict[str, Any] | None] = {"best": None, "declining": None, "worst": None}
        keys = ["best", "declining", "worst"]
        sections = re.split(r"\n\s*(?:【?[1-3]|\d+[\.\)．])", text)

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

    # ------------------------------------------------------------------ #
    # フォールバック定型文
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fallback(
        candidates: dict[str, HighlightCandidate | None],
    ) -> dict[str, dict[str, Any] | None]:
        """LLM を使わず数値ベースの定型文でハイライトを生成する。"""
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
