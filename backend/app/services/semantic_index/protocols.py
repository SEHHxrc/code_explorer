"""语义索引的结构化接口。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .contracts import (
    CallableFact,
    CallSiteFact,
    ParameterFact,
    ReturnFact,
    ValueSlotFact,
    VariableTypeFact,
)


@runtime_checkable
class SemanticIndexView(Protocol):
    """供图构建和安全分析消费的最小只读查询接口。"""

    @property
    def schema_version(self) -> str:
        """返回语义索引协议版本。"""
        ...

    def callable(self, symbol_id: str) -> CallableFact | None:
        """按稳定符号身份查询可调用对象。"""
        ...

    def calls_from(self, symbol_id: str) -> Iterable[CallSiteFact]:
        """按稳定顺序返回指定调用者中的调用点。"""
        ...

    def variable_type(self, owner_symbol: str, name: str) -> VariableTypeFact | None:
        """查询指定作用域内变量的类型事实。"""
        ...


@runtime_checkable
class SemanticValueFlowView(SemanticIndexView, Protocol):
    """在基础语义查询上增加跨过程值接口。"""

    def parameters_for(self, method_id: str) -> Iterable[ParameterFact]:
        """按声明顺序返回方法形参事实。"""
        ...

    def returns_for(self, method_id: str) -> Iterable[ReturnFact]:
        """按源码位置返回方法中的 return 事实。"""
        ...

    def call_result(self, callsite_id: str) -> ValueSlotFact | None:
        """返回调用表达式在调用者中的结果槽位。"""
        ...
