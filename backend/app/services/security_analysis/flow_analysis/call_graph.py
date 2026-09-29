"""跨过程安全数据流使用的调用边索引和语言参数绑定。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite
from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact

from ..registry import SemanticsRegistry


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
        for items in transitions.values():
            for transition in items:
                reverse[transition.target].append(transition)
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


class ProgramArgumentBinder:
    """把 ProgramGraph 调用实参按注册语言语义绑定到目标形参。"""

    def __init__(self, semantics: SemanticsRegistry | None = None) -> None:
        """保存可选语言语义注册表；缺失时使用保守公共绑定。"""
        self.semantics = semantics

    def bind(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[tuple[str, tuple[str, ...]]]:
        """返回目标形参与对应实参中词法变量的绑定。"""
        semantics = self.semantics.get(target.language) if self.semantics else None
        if semantics is None:
            return self._fallback(call, target)
        result: list[tuple[str, tuple[str, ...]]] = []
        for binding in semantics.bind_program_arguments(call, target):
            identifiers: list[str] = []
            if binding.argument_position is not None:
                position = binding.argument_position
                if 0 <= position < len(call.positional_arguments):
                    identifiers = call.positional_arguments[position]
            elif binding.argument_keyword:
                identifiers = call.keyword_arguments.get(binding.argument_keyword, [])
            names = tuple(dict.fromkeys(identifiers))
            if names:
                result.append((binding.parameter_name, names))
        return result

    @staticmethod
    def _fallback(
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[tuple[str, tuple[str, ...]]]:
        """未注册语言语义时仅绑定位置参数和名称完全一致的关键字参数。"""
        result: list[tuple[str, tuple[str, ...]]] = []
        for index, identifiers in enumerate(call.positional_arguments):
            if index >= len(target.parameters):
                break
            names = tuple(dict.fromkeys(identifiers))
            if names:
                result.append((target.parameters[index], names))
        parameter_names = set(target.parameters)
        for keyword, identifiers in call.keyword_arguments.items():
            names = tuple(dict.fromkeys(identifiers))
            if keyword in parameter_names and names:
                result.append((keyword, names))
        return result
