"""从分析 Artifact 构建一次、供多个只读工具共享的轻量索引。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SymbolRow:
    path: str
    symbol: dict[str, Any]


class ProjectEvidenceIndex:
    """保留原始对象引用，避免每次工具调用重新扫描完整符号和依赖边。"""

    def __init__(self, artifact: dict[str, Any]):
        self.symbols: list[SymbolRow] = []
        for path, symbols in (artifact.get("file_symbols") or {}).items():
            for symbol in symbols or []:
                if isinstance(symbol, dict):
                    self.symbols.append(SymbolRow(path=str(path), symbol=symbol))
        graph = artifact.get("dependency_graph") or {}
        self.nodes = {
            str(node.get("id")): node
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id") is not None
        }
        self.incoming: dict[str, list[dict]] = defaultdict(list)
        self.outgoing: dict[str, list[dict]] = defaultdict(list)
        for edge in graph.get("links", graph.get("edges", [])):
            if not isinstance(edge, dict):
                continue
            source = self._endpoint(edge.get("source"))
            target = self._endpoint(edge.get("target"))
            if not source or not target:
                continue
            self.outgoing[source].append(edge)
            self.incoming[target].append(edge)

    @staticmethod
    def _endpoint(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("id")
        return "" if value is None else str(value)