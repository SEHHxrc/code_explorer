"""各分析阶段共用的只读调用转换；不解析目标，不绑定语言参数，不执行污点传播。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .contracts import SemanticIndexArtifact
from .index import SemanticIndex


@dataclass(frozen=True)
class CallTransition:
    """一条已解析依赖调用边及其目标可信度。"""

    edge_id: str
    callsite_id: str
    source: str
    target: str
    certainty: str
    confidence: str
    truncated: bool


class CallTransitionIndex:
    """按调用者符号索引无损依赖图中的项目内 calls 边。"""

    def __init__(self, transitions: dict[str, tuple[CallTransition, ...]]) -> None:
        """保存已规范化且稳定排序的只读调用边集合。"""
        self._transitions = transitions
        reverse: dict[str, list[CallTransition]] = defaultdict(list)
        by_callsite: dict[tuple[str, str], list[CallTransition]] = defaultdict(list)
        for items in transitions.values():
            for transition in items:
                reverse[transition.target].append(transition)
                by_callsite[(transition.source, transition.callsite_id)].append(transition)
        self._by_callsite = {key: tuple(items) for key, items in by_callsite.items()}
        self._callers = {
            target: tuple(sorted(items, key=lambda item: (
                item.source,
                item.callsite_id,
                item.edge_id,
            )))
            for target, items in reverse.items()
        }

    @classmethod
    def from_dependency_graph(
        cls,
        dependency_graph: dict[str, Any],
    ) -> CallTransitionIndex:
        """提取带 callsite_id、确定性、置信度和截断状态的调用边。"""
        grouped: dict[str, list[CallTransition]] = defaultdict(list)
        raw_edges = dependency_graph.get(
            "links",
            dependency_graph.get("edges", []),
        ) or []
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("relation") or raw.get("type") or "") != "calls":
                continue
            source = cls._endpoint(raw.get("source"))
            target = cls._endpoint(raw.get("target"))
            callsite = raw.get("callsite") or {}
            callsite_id = str(raw.get("callsite_id") or callsite.get("id") or "")
            if not source or not target or not callsite_id:
                continue
            edge_id = str(raw.get("id") or raw.get("key") or "")
            if not edge_id:
                edge_id = "call-transition:" + cls._digest(
                    "\x1f".join((callsite_id, source, target))
                )
            certainty = str(raw.get("target_certainty") or "unresolved")
            confidence = str(raw.get("confidence") or "low")
            grouped[source].append(CallTransition(
                edge_id=edge_id,
                callsite_id=callsite_id,
                source=source,
                target=target,
                certainty=certainty if certainty in {"must", "may"} else "may",
                confidence=confidence if confidence in {"high", "medium", "low"} else "low",
                truncated=bool(raw.get("truncated")),
            ))
        return cls({
            source: tuple(sorted(items, key=lambda item: (
                item.callsite_id,
                item.target,
                item.edge_id,
            )))
            for source, items in grouped.items()
        })

    @classmethod
    def from_semantic_index(
        cls,
        semantic_index: SemanticIndex | SemanticIndexArtifact | dict[str, Any],
    ) -> CallTransitionIndex:
        """从公共语义索引读取调用目标，不再解释依赖图存储细节。"""
        index = SemanticIndex.load(semantic_index)
        grouped: dict[str, list[CallTransition]] = defaultdict(list)
        for callsite in index.artifact.callsites.values():
            for target in callsite.targets:
                if target.relation != "calls":
                    continue
                grouped[callsite.caller_symbol].append(CallTransition(
                    edge_id=target.edge_id,
                    callsite_id=callsite.callsite_id,
                    source=callsite.caller_symbol,
                    target=target.target_symbol,
                    certainty=target.certainty,
                    confidence=target.confidence,
                    truncated=target.truncated or callsite.truncated,
                ))
        return cls({
            source: tuple(sorted(items, key=lambda item: (
                item.callsite_id,
                item.target,
                item.edge_id,
            )))
            for source, items in grouped.items()
        })

    def for_source(self, symbol: str) -> tuple[CallTransition, ...]:
        """返回指定调用者的稳定调用边序列。"""
        return self._transitions.get(symbol, ())

    def for_target(self, symbol: str) -> tuple[CallTransition, ...]:
        """返回所有静态解析到指定目标的调用边。"""
        return self._callers.get(symbol, ())

    def for_callsite(self, caller_symbol: str, callsite_id: str) -> tuple[CallTransition, ...]:
        """按调用者与精确调用点查询全部已解析目标；空结果不证明无调用或代码安全。"""
        return self._by_callsite.get((caller_symbol, callsite_id), ())

    @classmethod
    def from_sources(
        cls, *, semantic_index: SemanticIndex | SemanticIndexArtifact | dict[str, Any] | None = None,
        dependency_graph: dict[str, Any] | None = None,
    ) -> CallTransitionIndex:
        """优先采用非空语义调用事实，旧产物回退依赖边；不混合两种目标集合或补造调用。"""
        if semantic_index is not None:
            index = SemanticIndex.load(semantic_index)
            if index.artifact.callsites:
                return cls.from_semantic_index(index)
        return cls.from_dependency_graph(dependency_graph or {})

    @staticmethod
    def _endpoint(value: Any) -> str:
        """读取 NetworkX node-link 端点的稳定字符串标识。"""
        if isinstance(value, dict):
            value = value.get("id")
        return "" if value is None else str(value)

    @staticmethod
    def _digest(value: str) -> str:
        """生成调用边缺省身份使用的稳定短摘要。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
