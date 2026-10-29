"""把依赖分析阶段产物规范化为共享语义索引。"""

from __future__ import annotations
from backend.app.services.value_binding.identities import value_identity, variable_root

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import TypeAdapter

from backend.app.services.program_index import ProgramIdentity

from .contracts import (
    CallableFact,
    CallableKind,
    CallRelation,
    CallSiteFact,
    Certainty,
    Confidence,
    ParameterFact,
    ResolvedTargetFact,
    ReturnFact,
    SemanticIndexArtifact,
    SemanticIndexCoverage,
    SemanticLocation,
    ValueSlotFact,
    VariableTypeFact,
)
from .index import SemanticIndex
from .hierarchy import type_facts
from .slots import (
    CALL_RESULT_SLOT,
    PARAMETER_SLOT,
    RETURN_SLOT,
    call_result_slot_id,
    parameter_slot_id,
    return_slot_id,
)

# 只共享编译后的校验器，不共享项目数据；约束与逐条构造 CallSiteFact 完全相同。
_CALLSITE_BATCH = TypeAdapter(dict[str, CallSiteFact])


@runtime_checkable
class SemanticIndexProvider(Protocol):
    """将某种静态分析实现投影为公共语义事实的接口。"""

    def build(
        self,
        *,
        definitions: Mapping[str, Any],
        references: Iterable[Any],
        variable_types: Mapping[str, Mapping[str, str]],
        graph: Any,
        unresolved: Iterable[Mapping[str, Any]],
    ) -> SemanticIndexArtifact:
        """输入实现私有分析状态，输出公共可持久化事实。"""
        ...


@runtime_checkable
class SemanticIndexEnricher(Protocol):
    """用另一种分析产物向既有语义索引追加事实的接口。"""

    def enrich(
        self,
        artifact: SemanticIndexArtifact,
        *,
        program_graph: Any,
    ) -> SemanticIndexArtifact:
        """返回追加事实后的新产物，不修改输入对象。"""
        ...


class ProgramGraphSemanticProvider:
    """把 ProgramGraph 已有形参、return 和调用结果投影为值接口。"""

    def enrich(
        self,
        artifact: SemanticIndexArtifact,
        *,
        program_graph: Any,
    ) -> SemanticIndexArtifact:
        """追加值传播事实；不重新读取源码或修改 ProgramGraph。"""
        parameters = dict(artifact.parameters)
        returns = dict(artifact.returns)
        slots = dict(artifact.value_slots)
        index = SemanticIndex(artifact)
        for function in sorted(
            program_graph.functions.values(),
            key=lambda item: item.method_id,
        ):
            function_location = self._location(function.location)
            entry = function.nodes[function.entry_node_id]
            return_slot = return_slot_id(function.method_id)
            return_nodes = [
                node for node in function.nodes.values()
                if node.kind == "return" and node.location is not None
            ]
            return_certainty = "must" if len(return_nodes) == 1 else "may"
            slots[return_slot] = ValueSlotFact(
                slot_id=return_slot,
                kind=RETURN_SLOT,
                owner_symbol=function.symbol_id,
                method_id=function.method_id,
                location=function_location,
                certainty=return_certainty,
            )
            for position, name in enumerate(function.parameters):
                slot_id = parameter_slot_id(function.method_id, position)
                parameter_types = index.variable_types_for(function.symbol_id, name, role="parameter")
                legacy_type = index.variable_type(function.symbol_id, name) if not parameter_types else None
                type_fact = (parameter_types[0] if len(parameter_types) == 1 else
                             legacy_type if legacy_type is not None and legacy_type.role == "unknown" else None)
                fact_id = "parameter:" + self._digest(slot_id)
                parameters[fact_id] = ParameterFact(
                    fact_id=fact_id,
                    slot_id=slot_id,
                    callable_symbol=function.symbol_id,
                    method_id=function.method_id,
                    name=name,
                    position=position,
                    language=function.language,
                    type_literal=type_fact.type_literal if type_fact else "",
                    declaration_id=entry.definition_declarations.get(variable_root(name), ""),
                    location=function_location,
                )
                slots[slot_id] = ValueSlotFact(
                    slot_id=slot_id,
                    kind=PARAMETER_SLOT,
                    owner_symbol=function.symbol_id,
                    method_id=function.method_id,
                    name=name,
                    position=position,
                    value_names=[name],
                    value_ids={name: value_identity(entry, name, write=True)}
                        if variable_root(name) in entry.definition_declarations else {},
                    location=function_location,
                )
            for node in sorted(function.nodes.values(), key=lambda item: item.node_id):
                if node.kind == "return" and node.location is not None:
                    fact_id = "return:" + self._digest(node.node_id)
                    returns[fact_id] = ReturnFact(
                        fact_id=fact_id,
                        slot_id=return_slot,
                        callable_symbol=function.symbol_id,
                        method_id=function.method_id,
                        node_id=node.node_id,
                        language=function.language,
                        location=self._location(node.location),
                        value_names=list(node.uses),
                        callsite_ids=[item.callsite_id for item in node.calls],
                        certainty=return_certainty,
                    )
                if not node.definitions or not node.calls or node.location is None:
                    continue
                certainty = "must" if len(node.calls) == 1 else "may"
                for call in node.calls:
                    slot_id = call_result_slot_id(call.callsite_id)
                    slots[slot_id] = ValueSlotFact(
                        slot_id=slot_id,
                        kind=CALL_RESULT_SLOT,
                        owner_symbol=function.symbol_id,
                        method_id=function.method_id,
                        callsite_id=call.callsite_id,
                        node_id=node.node_id,
                        value_names=list(node.definitions),
                        value_ids={name: value_identity(node, name, write=True) for name in node.definitions
                                   if variable_root(name) in node.definition_declarations} if len(node.calls) == 1 else {},
                        location=self._location(call.location),
                        certainty=certainty,
                    )
        coverage = artifact.coverage.model_copy(update={
            "parameter_count": len(parameters),
            "return_count": len(returns),
            "value_slot_count": len(slots),
        })
        limitations = [
            item for item in artifact.limitations
            if "参数、返回值" not in item
        ]
        limitations.append(
            "返回值槽位当前覆盖局部变量接收的直接调用；字段、容器元素和多调用表达式按保守 may 处理。"
        )
        return artifact.model_copy(update={
            "schema_version": "1.6" if artifact.schema_version == "1.6" else "1.5" if artifact.types else "1.4" if any(item.declaration_id for item in parameters.values()) or any(item.value_ids for item in slots.values())
                else artifact.schema_version if artifact.schema_version in {"1.2", "1.3", "1.4", "1.5"} else "1.1",
            "parameters": parameters,
            "returns": returns,
            "value_slots": slots,
            "coverage": coverage,
            "limitations": list(dict.fromkeys(limitations)),
        })

    @staticmethod
    def _location(location: Any) -> SemanticLocation:
        """把 ProgramGraph 位置投影为语义索引位置。"""
        return SemanticLocation.model_validate(location.model_dump())

    @staticmethod
    def _digest(value: str) -> str:
        """生成事实身份使用的稳定短摘要。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


class DependencyAnalysisSemanticProvider:
    """从依赖分析器的定义、引用、类型表和无损图生成事实。"""

    CALLSITE_BATCH_SIZE = 4096

    def build(
        self,
        *,
        definitions: Mapping[str, Any],
        references: Iterable[Any],
        variable_types: Mapping[str, Mapping[str, str]],
        graph: Any,
        unresolved: Iterable[Mapping[str, Any]],
    ) -> SemanticIndexArtifact:
        """生成语义索引；只读取传入状态，不改变依赖图。"""
        callables = self._callables(definitions)
        reference_index = {
            self._callsite_id(reference): reference
            for reference in references
            if getattr(reference, "kind", "") in {"call", "new"}
        }
        targets: dict[str, list[ResolvedTargetFact]] = defaultdict(list)
        for source, target, key, attributes in graph.edges(keys=True, data=True):
            raw_relation = str(attributes.get("relation") or "")
            callsite_id = str(attributes.get("callsite_id") or "")
            if raw_relation not in {"calls", "instantiates"} or not callsite_id:
                continue
            relation: CallRelation = "calls" if raw_relation == "calls" else "instantiates"
            edge_id = str(attributes.get("id") or key or "")
            targets[callsite_id].append(ResolvedTargetFact(
                edge_id=edge_id or self._stable_id(callsite_id, str(source), str(target)),
                target_symbol=str(target),
                relation=relation,
                dispatch=str(attributes.get("dispatch") or "unknown"),
                resolution_method=str(attributes.get("resolution_method") or "unknown"),
                certainty=self._certainty(attributes.get("target_certainty")),
                confidence=self._confidence(attributes.get("confidence")),
                truncated=bool(attributes.get("truncated")),
                fallback_reason=attributes.get("fallback_reason"),
            ))

        unresolved_by_id = {
            str(item.get("callsite_id") or ""): item
            for item in unresolved
            if item.get("callsite_id")
        }
        all_callsite_ids = set(reference_index) | set(targets) | set(unresolved_by_id)
        callsites: dict[str, CallSiteFact] = {}
        pending: dict[str, dict[str, Any]] = {}
        for callsite_id in sorted(all_callsite_ids):
            reference = reference_index.get(callsite_id)
            missing = unresolved_by_id.get(callsite_id, {})
            location = self._reference_location_data(reference, missing)
            resolved_targets = self._unique_targets(targets.get(callsite_id, []))
            pending[callsite_id] = dict(
                callsite_id=callsite_id,
                caller_symbol=str(
                    getattr(reference, "from_fqn", "") or missing.get("from_fqn") or ""
                ),
                language=str(getattr(reference, "lang", "") or missing.get("language") or ""),
                name=str(getattr(reference, "name", "") or missing.get("name") or ""),
                receiver=str(
                    getattr(reference, "receiver", "") or missing.get("receiver") or ""
                ),
                location=location,
                targets=resolved_targets,
                unresolved_reason=(
                    str(missing.get("unresolved_reason"))
                    if missing.get("unresolved_reason") else None
                ),
                truncated=any(item.truncated for item in resolved_targets),
                fallback_reason=(missing.get("fallback_reason") or next(
                    (item.fallback_reason for item in resolved_targets if item.fallback_reason), None)),
            )
            if len(pending) >= self.CALLSITE_BATCH_SIZE:
                callsites.update(_CALLSITE_BATCH.validate_python(pending))
                pending.clear()
        if pending:
            callsites.update(_CALLSITE_BATCH.validate_python(pending))

        types: dict[str, VariableTypeFact] = {}
        for owner_symbol in sorted(variable_types):
            language = self._owner_language(owner_symbol, definitions)
            for name, type_literal in sorted(variable_types[owner_symbol].items()):
                if not name or not type_literal:
                    continue
                key = SemanticIndex.variable_type_key(owner_symbol, name)
                types[key] = VariableTypeFact(
                    fact_id="vartype:" + self._digest(key),
                    owner_symbol=owner_symbol,
                    name=name,
                    language=language,
                    type_literal=str(type_literal),
                )

        resolved_count = sum(len(item.targets) for item in callsites.values())
        unresolved_count = sum(not item.targets for item in callsites.values())
        project_types = type_facts(definitions, graph.edges(data=True))
        return SemanticIndexArtifact(
            schema_version="1.5" if project_types else "1.0",
            types=project_types,
            callables=callables,
            callsites=callsites,
            variable_types=types,
            coverage=SemanticIndexCoverage(
                callable_count=len(callables),
                callsite_count=len(callsites),
                resolved_target_count=resolved_count,
                unresolved_callsite_count=unresolved_count,
                variable_type_count=len(types),
            ),
            limitations=[
                "当前索引复用可调用对象、调用目标和变量声明类型；参数、返回值及字段别名事实将在后续版本补充。",
                "调用目标来自依赖分析的静态解析，动态派发、反射和运行时加载仍可能不完整。",
            ],
        )

    @staticmethod
    def _callables(definitions: Mapping[str, Any]) -> dict[str, CallableFact]:
        """投影函数、方法和构造器定义。"""
        result: dict[str, CallableFact] = {}
        for symbol_id, definition in sorted(definitions.items()):
            raw_kind = str(getattr(definition, "kind", ""))
            if raw_kind not in {"function", "method", "constructor"}:
                continue
            kind: CallableKind = (
                "function" if raw_kind == "function"
                else "method" if raw_kind == "method"
                else "constructor"
            )
            path = ProgramIdentity.file_id(str(getattr(definition, "file", "")))
            line = max(0, int(getattr(definition, "line", 0) or 0))
            end_line = max(line, int(getattr(definition, "end_line", 0) or line))
            result[symbol_id] = CallableFact(
                symbol_id=symbol_id,
                name=str(getattr(definition, "name", "")),
                kind=kind,
                language=str(getattr(definition, "lang", "")),
                location=SemanticLocation(
                    path=path,
                    line=line,
                    end_line=end_line,
                    location_id=ProgramIdentity.location_id(path, line, 0, end_line, 0),
                ),
                return_type=str(getattr(definition, "return_type", "") or ""),
            )
        return result

    @staticmethod
    def _callsite_id(reference: Any) -> str:
        """由依赖引用位置生成跨模块稳定调用点身份。"""
        return ProgramIdentity.callsite_id(
            str(getattr(reference, "file", "")),
            int(getattr(reference, "line", 0) or 0),
            int(getattr(reference, "column", 0) or 0),
            int(getattr(reference, "end_line", 0) or 0),
            int(getattr(reference, "end_column", 0) or 0),
        )

    @staticmethod
    def _reference_location_data(reference: Any | None, fallback: Mapping[str, Any]) -> dict[str, Any]:
        """组装原位置字段，由同一模型在有界批次中校验，避免重复 Python 构造层。"""
        def value(name: str) -> Any:
            """读取引用字段；仅在引用缺失时查询诊断字典。"""
            return getattr(reference, name, None) if reference is not None else fallback.get(name)

        path = ProgramIdentity.file_id(str(value("file") or ""))
        line = max(0, int(value("line") or 0))
        column = max(0, int(value("column") or 0))
        end_line = max(0, int(value("end_line") or line))
        end_column = max(0, int(value("end_column") or column))
        return dict(
            path=path,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
            location_id=ProgramIdentity.location_id(
                path, line, column, end_line, end_column,
            ),
        )

    @staticmethod
    def _unique_targets(targets: list[ResolvedTargetFact]) -> list[ResolvedTargetFact]:
        """按边身份去重并稳定排序候选目标。"""
        unique = {item.edge_id: item for item in targets}
        return sorted(unique.values(), key=lambda item: (
            item.target_symbol,
            item.edge_id,
        ))

    @staticmethod
    def _certainty(value: Any) -> Certainty:
        """把旧值保守规范成 must/may。"""
        return "must" if str(value) == "must" else "may"

    @staticmethod
    def _confidence(value: Any) -> Confidence:
        """把未知置信度保守降级为 low。"""
        normalized = str(value or "low")
        if normalized == "high":
            return "high"
        if normalized == "medium":
            return "medium"
        return "low"

    @staticmethod
    def _owner_language(owner: str, definitions: Mapping[str, Any]) -> str:
        """从拥有者或最近定义推断变量类型事实所属语言。"""
        definition = definitions.get(owner)
        if definition is not None:
            return str(getattr(definition, "lang", ""))
        return next((str(getattr(item, "lang", "")) for symbol, item in definitions.items()
            if owner.startswith(symbol + "::") or symbol.startswith(owner + "::")
        ), "")

    @classmethod
    def _stable_id(cls, *parts: str) -> str:
        """为缺少原始边身份的兼容数据生成稳定标识。"""
        return "semantic-edge:" + cls._digest("\x1f".join(parts))

    @staticmethod
    def _digest(value: str) -> str:
        """生成紧凑且跨运行稳定的 SHA-256 摘要。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
