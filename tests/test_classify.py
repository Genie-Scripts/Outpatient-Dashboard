"""src/core/classify.py の単体テスト。"""
from pathlib import Path

from src.core.classify import DeptClassifier


def test_load_and_query(tmp_path: Path) -> None:
    import pandas as pd

    path = tmp_path / "dept.csv"
    pd.DataFrame([
        {"診療科名": "泌尿器科", "タイプ": "外科系", "診療科コード": "U",
         "表示順": 1, "評価対象": "TRUE", "備考": ""},
        {"診療科名": "入退院支援センター", "タイプ": "その他", "診療科コード": "AD",
         "表示順": 18, "評価対象": "FALSE", "備考": "運用系"},
    ]).to_csv(path, index=False, encoding="utf-8-sig")

    c = DeptClassifier(path)
    assert c.get_type("泌尿器科") == "外科系"
    assert c.get_code("泌尿器科") == "U"
    assert c.is_evaluation_target("泌尿器科") is True
    assert c.is_evaluation_target("入退院支援センター") is False
    assert c.get_type("存在しない科") == "その他"

    targets = c.evaluation_targets()
    assert len(targets) == 1
    assert targets[0].name == "泌尿器科"
