"""Ollama / OpenAI互換APIクライアント。

config/llm_config.yaml から接続設定を読み込み、ハイライト候補から
見出し（HEAD）+ 本文（BODY）の自然文を生成する。

Ollama が未起動の場合は `ollama serve` を自動起動し、最大 _STARTUP_TIMEOUT 秒待機する。
起動失敗・モデル未取得・API エラーの場合は定型文フォールバック。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml

from src.core.highlights import HighlightCandidate

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT: int = 30   # 秒: ollama serve 起動待ちの上限
_HEALTH_INTERVAL: int = 1    # 秒: ヘルスチェックのポーリング間隔

# 観察コメント生成のデフォルトパラメータ
# 非思考型 instruct モデル（Swallow / Qwen Instruct 等）前提。
# 出力は OBSERVATION 1 行のみ（句点で終端）なので max_tokens は短く縛る。
_OBS_TEMPERATURE: float = 0.2
_OBS_MAX_TOKENS: int = 300
# プロンプトテンプレや出力規約を変えたら必ず上げる（既存キャッシュを無効化するため）
_OBS_PROMPT_VERSION: str = "v2"


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

    def generate_observation(
        self,
        section: str,
        facts: dict[str, Any],
        instruction: str,
        fallback: Callable[[], str],
        cache_dir: Path | None = None,
        cache_subkey: str | None = None,
    ) -> str:
        """観察ファクトから 1〜2 文のコメントを生成する。

        Args:
            section: セクション識別子（"drug_revisit" など）。プロンプトのスコープ表示に使う。
            facts: Python 側で計算済みの事実 dict（数値・短い文字列のみ）。
                LLM はこの dict 内の値以外を参照／改変してはならない。
            instruction: 「何を 1 文で書いてほしいか」の依頼文。
            fallback: LLM 不在時の定型文を返す引数なし関数。失敗時はこれの戻り値を採用。
            cache_dir: キャッシュ保存ディレクトリ。None ならキャッシュ無効。
            cache_subkey: キャッシュキーの先頭に挟む補助識別子（月＋科コードなど）。

        Returns:
            観察コメント（1〜2 文）。LLM 失敗・無効時は fallback() の戻り値。
        """
        cache_key = self._observation_cache_key(section, facts, instruction)
        cache_path = (
            (cache_dir / (cache_subkey or "_") / f"{cache_key}.json")
            if cache_dir is not None
            else None
        )
        if cache_path and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and isinstance(cached.get("comment"), str):
                    return cached["comment"]
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("LLMキャッシュ読込失敗（再生成）: %s", e)

        if not self.enabled:
            comment = fallback()
            self._write_observation_cache(cache_path, section, facts, instruction, comment, source="fallback")
            return comment

        if not self._ensure_server() or not self._is_model_available():
            logger.warning("LLM 利用不可 → フォールバック（section=%s）", section)
            comment = fallback()
            self._write_observation_cache(cache_path, section, facts, instruction, comment, source="fallback")
            return comment

        prompt = self._build_observation_prompt(section, facts, instruction)
        try:
            text = self._call_llm_with_params(
                prompt,
                temperature=_OBS_TEMPERATURE,
                max_tokens=_OBS_MAX_TOKENS,
            )
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
            logger.warning("LLM 呼び出し失敗 → フォールバック（section=%s）: %s", section, e)
            comment = fallback()
            self._write_observation_cache(cache_path, section, facts, instruction, comment, source="fallback")
            return comment

        parsed = self._parse_observation(text)
        if parsed is None:
            logger.warning("LLM 応答を解析できず → フォールバック（section=%s）", section)
            comment = fallback()
            self._write_observation_cache(cache_path, section, facts, instruction, comment, source="fallback")
            return comment
        self._write_observation_cache(cache_path, section, facts, instruction, parsed, source="llm")
        return parsed

    @staticmethod
    def _observation_cache_key(section: str, facts: dict[str, Any], instruction: str) -> str:
        """sha256(section|prompt_version|model 抜き — model はディレクトリ分離前提) でキー化。

        プロンプトテンプレ更新時はキャッシュを無効化するため、_OBS_PROMPT_VERSION を含める。
        モデル切替はキャッシュディレクトリを別にする運用（cache_dir に model 名を含める）を推奨。
        """
        payload = json.dumps(
            {
                "section": section,
                "prompt_version": _OBS_PROMPT_VERSION,
                "instruction": instruction,
                "facts": facts,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _write_observation_cache(
        cache_path: Path | None,
        section: str,
        facts: dict[str, Any],
        instruction: str,
        comment: str,
        source: str,
    ) -> None:
        if cache_path is None:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "section": section,
                        "prompt_version": _OBS_PROMPT_VERSION,
                        "instruction": instruction,
                        "facts": facts,
                        "comment": comment,
                        "source": source,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug("LLMキャッシュ書込失敗（無視）: %s", e)

    @staticmethod
    def _build_observation_prompt(section: str, facts: dict[str, Any], instruction: str) -> str:
        facts_json = json.dumps(facts, ensure_ascii=False, indent=2)
        # Llama 系は "最後の指示" を強く守る傾向があるため、
        # 出力規約は冒頭と末尾の両方で繰り返す（Swallow 継続事前学習の format-following 緩和への保険）。
        return (
            f"セクション: {section}\n"
            f"以下は Python が計算済みの事実です。これらの数値・文字列以外の情報を一切付け加えず、"
            f"数値も改変せず、改行を含まない 1 文（最大 2 文）で観察コメントを書いてください。\n"
            f"出力フォーマットは厳密に `OBSERVATION: <本文>` の 1 行のみ。\n\n"
            f"FACTS:\n{facts_json}\n\n"
            f"INSTRUCTION: {instruction}\n\n"
            f"重要な出力規約（厳守）:\n"
            f"- 出力は `OBSERVATION: ` で始まる 1 行のみ\n"
            f"- 改行を含めない\n"
            f"- 文末は必ず句点（。）で終わる\n"
            f"- 日本語で 1 文（最大 2 文、合計 80 字以内が目安）\n"
            f"- FACTS に無い数値・比較・推測を一切書かない\n"
        )

    @staticmethod
    def _parse_observation(text: str) -> str | None:
        """`OBSERVATION: ...` 形式から本文だけ取り出す。

        思考型モデルが CoT 中で "OBSERVATION:" にメタ言及するケースに備え、
        最後の出現を採用する。本文に CoT アーティファクト（`<0x0A>`, `<|`, `...` など
        プレースホルダ）が残っている場合や、文末記号で終わっていない場合は
        信頼できない出力としてフォールバックさせる。
        """
        matches = list(re.finditer(r"OBSERVATION[:：]\s*(.+)", text))
        if not matches:
            return None
        body = matches[-1].group(1).strip()
        body = body.split("\n", 1)[0].strip()
        if not body:
            return None
        # CoT 由来のアーティファクト混入をはじく
        artifacts = ("<0x", "<|", "<text>", "<think>", "...", '"...')
        if any(a in body for a in artifacts):
            return None
        # 観察コメントは句点で終わるはず（中途半端な打ち切りをはじく）
        if not body.endswith(("。", "．", ".", "！", "!", "？", "?")):
            return None
        return body

    def _call_llm_with_params(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """generate_highlights 系と異なるパラメータで OpenAI 互換エンドポイントを叩く。"""
        body = json.dumps({
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.config.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        logger.info("LLM呼び出し（観察）: %s", self.config.endpoint)
        with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]

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
