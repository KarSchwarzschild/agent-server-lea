"""
评测数据集加载器。

支持 JSON 格式的评测数据集：
[
  {
    "input_text": "帮我查找关于教育心理学的论文",
    "expected_intent": "skill",
    "expected_skill": "academic-search",
    "confidence_level": "high"
  },
  ...
]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalSample:
    """单个评测样本。"""
    input_text: str
    expected: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class EvalDataset:
    """评测数据集。"""
    name: str
    samples: list[EvalSample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: str | Path) -> EvalDataset:
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))

        if isinstance(raw, list):
            samples_raw = raw
            meta = {}
        else:
            samples_raw = raw.get("samples", raw.get("data", []))
            meta = {k: v for k, v in raw.items() if k not in ("samples", "data")}

        samples = []
        for i, item in enumerate(samples_raw):
            text = item.pop("input_text", item.pop("input", item.pop("query", "")))
            sample_id = item.pop("id", str(i))
            sample_meta = item.pop("metadata", {})

            samples.append(EvalSample(
                input_text=text,
                expected=item,
                metadata=sample_meta,
                id=sample_id,
            ))

        return cls(name=path.stem, samples=samples, metadata=meta)

    def __len__(self) -> int:
        return len(self.samples)

    def filter_by(self, key: str, value: Any) -> EvalDataset:
        filtered = [s for s in self.samples if s.expected.get(key) == value or s.metadata.get(key) == value]
        return EvalDataset(name=f"{self.name}[{key}={value}]", samples=filtered, metadata=self.metadata)
