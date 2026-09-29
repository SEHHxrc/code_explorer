"""可持久化语义索引的只读查询实现。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import (
    CallableFact,
    CallSiteFact,
    ParameterFact,
    ReturnFact,
    SemanticIndexArtifact,
    ValueSlotFact,
    VariableTypeFact,
)
from .slots import call_result_slot_id


class SemanticIndex:
    """在不可变事实产物上建立轻量查询表，不复制图算法。"""

    def __init__(self, artifact: SemanticIndexArtifact) -> None:
        """输入已校验产物并预建调用者索引。"""
        self.artifact = artifact
        grouped: dict[str, list[CallSiteFact]] = defaultdict(list)
        for callsite in artifact.callsites.values():
            grouped[callsite.caller_symbol].append(callsite)
        self._calls_by_caller = {
            symbol: tuple(sorted(items, key=lambda item: (
                item.location.path,
                item.location.line,
                item.location.column,
                item.callsite_id,
            )))
            for symbol, items in grouped.items()
        }
        parameters: dict[str, list[ParameterFact]] = defaultdict(list)
        for parameter in artifact.parameters.values():
            parameters[parameter.method_id].append(parameter)
        self._parameters_by_method = {
            method_id: tuple(sorted(items, key=lambda item: (item.position, item.fact_id)))
            for method_id, items in parameters.items()
        }
        returns: dict[str, list[ReturnFact]] = defaultdict(list)
        for return_fact in artifact.returns.values():
            returns[return_fact.method_id].append(return_fact)
        self._returns_by_method = {
            method_id: tuple(sorted(items, key=lambda item: (
                item.location.path,
                item.location.line,
                item.location.column,
                item.fact_id,
            )))
            for method_id, items in returns.items()
        }

    @classmethod
    def load(
        cls,
        value: SemanticIndex | SemanticIndexArtifact | Mapping[str, Any],
    ) -> SemanticIndex:
        """统一接收查询对象、Pydantic 产物或持久化字典。"""
        if isinstance(value, cls):
            return value
        if isinstance(value, SemanticIndexArtifact):
            return cls(value)
        return cls(SemanticIndexArtifact.model_validate(value))

    @property
    def schema_version(self) -> str:
        """返回当前产物协议版本。"""
        return self.artifact.schema_version

    def callable(self, symbol_id: str) -> CallableFact | None:
        """按稳定符号身份查询可调用对象。"""
        return self.artifact.callables.get(symbol_id)

    def calls_from(self, symbol_id: str) -> Iterable[CallSiteFact]:
        """按源码位置稳定返回指定调用者的调用点。"""
        return self._calls_by_caller.get(symbol_id, ())

    def variable_type(self, owner_symbol: str, name: str) -> VariableTypeFact | None:
        """按作用域和变量名查询类型事实。"""
        return self.artifact.variable_types.get(self.variable_type_key(owner_symbol, name))

    def parameters_for(self, method_id: str) -> Iterable[ParameterFact]:
        """按声明位置返回方法形参事实。"""
        return self._parameters_by_method.get(method_id, ())

    def returns_for(self, method_id: str) -> Iterable[ReturnFact]:
        """按源码位置返回方法的全部 return 事实。"""
        return self._returns_by_method.get(method_id, ())

    def call_result(self, callsite_id: str) -> ValueSlotFact | None:
        """按调用点查询调用结果槽位。"""
        return self.artifact.value_slots.get(call_result_slot_id(callsite_id))

    @staticmethod
    def variable_type_key(owner_symbol: str, name: str) -> str:
        """生成无歧义的变量类型查询键。"""
        return owner_symbol + "\x1f" + name
