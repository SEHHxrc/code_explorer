"""公共事实的文件、实体和调用投影；旧图仅在对应事实缺失时兼容读取。"""

from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from .contracts import SemanticIndexArtifact
from .index import SemanticIndex
from backend.app.services.program_index import ProgramIdentity


class ProgramFacts:
    """为消费者隔离事实存储，不解析源码、不搜索路径，也不推断传播。"""

    def __init__(self, semantic_index: SemanticIndex | SemanticIndexArtifact | dict[str, Any] | None = None,
                 legacy_graph: dict[str, Any] | None = None) -> None:
        """接收公共索引及可选旧图；明确记录的空文件范围不能回退成全盘扫描。"""
        self.index = (semantic_index.artifact if isinstance(semantic_index, SemanticIndex) else semantic_index
                      if isinstance(semantic_index, SemanticIndexArtifact) else SemanticIndexArtifact.model_validate(semantic_index or {}))
        self.legacy = legacy_graph or {}

    def files_by_language(self) -> dict[str, list[str]] | None:
        """返回已记录文件范围；两种产物都没有范围时返回 None，交由文件发现处理。"""
        grouped: dict[str, list[str]] = defaultdict(list)
        if self.index.files is not None:
            for path, language in self.index.files.items():
                if path and language:
                    grouped[language.lower()].append(ProgramIdentity.file_id(path))
        elif self.legacy:
            for node in self.legacy.get("nodes", []):
                if node.get("kind", node.get("type")) == "module":
                    language = str(node.get("lang", "")).lower()
                    path = ProgramIdentity.file_id(str(node.get("file") or node["id"]))
                    if language and path:
                        grouped[language].append(path)
        else:
            return None
        return {language: sorted(set(paths)) for language, paths in grouped.items()}

    def call_edges(self) -> Iterator[dict[str, Any]]:
        """逐条投影真实调用点，保留位置、解析来源和 may/must；不混合新旧目标。"""
        if self.index.callsites:
            for call in self.index.callsites.values():
                for target in call.targets:
                    yield {"id": target.edge_id, "source": call.caller_symbol, "target": target.target_symbol,
                           "relation": target.relation, "callsite_id": call.callsite_id,
                           "callsite": call.location.model_dump(), "dispatch": target.dispatch,
                           "target_certainty": target.certainty, "confidence": target.confidence,
                           "resolution_method": target.resolution_method, "origin": target.provenance,
                           "truncated": call.truncated or target.truncated,
                           "unresolved_reason": call.unresolved_reason, "fallback_reason": target.fallback_reason}
        else:
            yield from (edge for edge in self.legacy.get("links", self.legacy.get("edges", []))
                        if edge.get("relation", edge.get("type")) in {"calls", "instantiates"})

    def nodes(self) -> Iterator[dict[str, Any]]:
        """投影已保存声明用于展示；语义身份优先，旧图只补缺失实体。"""
        seen: set[str] = set()
        for item in self.index.callables.values():
            seen.add(item.symbol_id)
            yield {"id": item.symbol_id, "name": item.name, "kind": item.kind, "level": item.kind,
                   "file": item.location.path, "line": item.location.line, "lang": item.language}
        for node in self.legacy.get("nodes", []):
            if str(node.get("id")) not in seen:
                yield node
