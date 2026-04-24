"""診療科分類の参照API。

config/dept_classification.csv を読み込み、診療科名から
タイプ（内科系/外科系/その他）・診療科コード・評価対象フラグを返す。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class DeptInfo:
    """診療科メタ情報。"""

    name: str
    type: str
    code: str
    display_order: int
    is_evaluation_target: bool
    note: str = ""


class DeptClassifier:
    """診療科分類の参照クラス。"""

    def __init__(self, classification_path: Path) -> None:
        self._path = classification_path
        self._by_name: dict[str, DeptInfo] = {}
        self._load()

    def _load(self) -> None:
        df = pd.read_csv(self._path, encoding="utf-8-sig")
        for _, row in df.iterrows():
            info = DeptInfo(
                name=str(row["診療科名"]),
                type=str(row["タイプ"]),
                code=str(row["診療科コード"]),
                display_order=int(row["表示順"]),
                is_evaluation_target=str(row["評価対象"]).strip().upper() == "TRUE",
                note=str(row.get("備考", "") or ""),
            )
            self._by_name[info.name] = info

    def get(self, dept_name: str) -> DeptInfo | None:
        return self._by_name.get(dept_name)

    def get_type(self, dept_name: str) -> str:
        info = self._by_name.get(dept_name)
        return info.type if info else "その他"

    def get_code(self, dept_name: str) -> str:
        info = self._by_name.get(dept_name)
        return info.code if info else "XX"

    def is_evaluation_target(self, dept_name: str) -> bool:
        info = self._by_name.get(dept_name)
        return info.is_evaluation_target if info else False

    def evaluation_targets(self) -> list[DeptInfo]:
        return [
            info for info in sorted(self._by_name.values(), key=lambda x: x.display_order)
            if info.is_evaluation_target
        ]
