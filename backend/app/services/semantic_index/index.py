"""可持久化语义索引的只读查询实现。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from backend.app.services.program_index import ProgramIdentity

from .contracts import (
    CallableFact,
    CallSiteFact,
    ParameterFact,
    ReturnFact,
    SemanticIndexArtifact,
    SemanticLocation,
    ValueSlotFact,
    VariableTypeFact,
    VariableRole,
    SSAFunctionFact,
    VariableDeclarationFact,
    VariableReferenceFact,
)
from .slots import call_result_slot_id
from .locations import matches_location
from .references import VariableReferenceIndex


class SemanticIndex:
    """在不可变事实产物上建立轻量查询表，不复制图算法。"""

    def __init__(self, artifact: SemanticIndexArtifact) -> None:
        """输入已校验产物并预建调用者索引。"""
        self.artifact = artifact
        self._references = VariableReferenceIndex(artifact)
        grouped: dict[str, list[CallSiteFact]] = defaultdict(list)
        by_line: dict[tuple[str, int], list[CallSiteFact]] = defaultdict(list)
        for callsite in artifact.callsites.values():
            grouped[callsite.caller_symbol].append(callsite)
            by_line[(ProgramIdentity.file_id(callsite.location.path), callsite.location.line)].append(callsite)
        self._calls_by_line = {
            key: tuple(sorted(items, key=lambda item: (item.location.column, item.location.end_line,
                                                      item.location.end_column, item.callsite_id)))
            for key, items in by_line.items()
        }
        self._calls_by_caller = {
            symbol: tuple(sorted(items, key=lambda item: (
                item.location.path,
                item.location.line,
                item.location.column,
                item.callsite_id,
            )))
            for symbol, items in grouped.items()
        }
        types_by_name: dict[tuple[str, str], list[VariableTypeFact]] = defaultdict(list)
        types_by_line: dict[tuple[str, int], list[VariableTypeFact]] = defaultdict(list)
        for fact in artifact.variable_types.values():
            types_by_name[(fact.owner_symbol, fact.name)].append(fact)
            if fact.location is not None:
                types_by_line[(ProgramIdentity.file_id(fact.location.path), fact.location.line)].append(fact)
        self._types_by_name = {key: tuple(sorted(items, key=lambda item: item.fact_id))
                               for key, items in types_by_name.items()}
        self._types_by_line = {key: tuple(sorted(items, key=lambda item: item.fact_id))
                               for key, items in types_by_line.items()}
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

    def callsite(self, callsite_id: str) -> CallSiteFact | None:
        """按稳定身份读取调用事实，包括没有目标或解析失败的调用。"""
        return self.artifact.callsites.get(callsite_id)

    def calls_at(self, location: SemanticLocation, *, caller_symbol: str | None = None) -> tuple[CallSiteFact, ...]:
        """按起始行和已知坐标筛选调用；零坐标未知，歧义返回全部，未知行不猜测。"""
        if location.line == 0:
            return ()
        candidates = self._calls_by_line.get((ProgramIdentity.file_id(location.path), location.line), ())
        return tuple(item for item in candidates
                     if (caller_symbol is None or item.caller_symbol == caller_symbol)
                     and matches_location(location, item.location))

    def variable_type(self, owner_symbol: str, name: str) -> VariableTypeFact | None:
        """兼容名字查询仅返回唯一事实；同名多声明即使类型相同也不猜测使用点绑定。"""
        matches = self.variable_types_for(owner_symbol, name)
        return matches[0] if len(matches) == 1 else None

    def variable_types_for(self, owner_symbol: str, name: str, *, role: VariableRole | None = None) -> tuple[VariableTypeFact, ...]:
        """返回名字槽内全部声明事实，可按明确角色筛选；不是变量使用点的类型证明。"""
        matches = self._types_by_name.get((owner_symbol, name), ())
        return tuple(item for item in matches if role is None or item.role == role)

    def variable_types_at(
        self, declaration_location: SemanticLocation, *, owner_symbol: str | None = None,
        name: str | None = None,
    ) -> tuple[VariableTypeFact, ...]:
        """按声明范围查询全部类型事实；不以最近行号推断变量使用点的作用域。"""
        if declaration_location.line == 0:
            return ()
        matches = self._types_by_line.get((ProgramIdentity.file_id(declaration_location.path), declaration_location.line), ())
        return tuple(item for item in matches if item.location is not None
                     and (owner_symbol is None or item.owner_symbol == owner_symbol)
                     and (name is None or item.name == name)
                     and matches_location(declaration_location, item.location))

    def native_ssa_for(self, symbol_id: str) -> SSAFunctionFact | None:
        """读取已唯一映射的原生 SSA；缺失表示未覆盖，不替换成推测结果。"""
        return self.artifact.native_ssa.get(symbol_id)

    def variable_declaration(self, declaration_id: str) -> VariableDeclarationFact | None:
        """按稳定身份查询源码实体，不返回运行时对象或赋值版本。"""
        return self.artifact.variable_declarations.get(declaration_id)

    def variable_reference(self, reference_id: str) -> VariableReferenceFact | None:
        """查询一次变量标识符引用及其绑定/失败原因。"""
        return self.artifact.variable_references.get(reference_id)

    def variable_references_at(self, location: SemanticLocation, *, owner_symbol: str | None = None) -> tuple[VariableReferenceFact, ...]:
        """按引用坐标返回全部匹配，不按最近声明或同名猜测绑定。"""
        return self._references.at(location, owner_symbol)

    def variable_references_to(self, declaration_id: str) -> tuple[VariableReferenceFact, ...]:
        """反查源码声明的变量引用，不将写引用当成值流读边。"""
        return self._references.to(declaration_id)

    def declaration_types_for_reference(self, reference_id: str) -> tuple[VariableTypeFact, ...]:
        """读取绑定声明的类型候选，不冒充使用点流敏感类型或运行时值。"""
        return self._references.declaration_types(reference_id)

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
        """生成旧格式名字槽键，不代表声明身份；新调用方使用查询接口而非字典键。"""
        return owner_symbol + "\x1f" + name
