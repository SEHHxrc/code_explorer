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
    SSAFunctionFact,
    SemanticLocation,
    VariableRole,
    VariableDeclarationFact,
    VariableReferenceFact,
)
from .calls import CallTransition


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
class CallTransitionView(Protocol):
    """框架参数转发与值传播共用的只读调用查询，不依赖任何安全求解器。"""

    def for_source(self, symbol: str) -> tuple[CallTransition, ...]:
        """返回指定调用者的已有调用边。"""
        ...

    def for_target(self, symbol: str) -> tuple[CallTransition, ...]:
        """返回指定目标的已有入边。"""
        ...

    def for_callsite(self, caller_symbol: str, callsite_id: str) -> tuple[CallTransition, ...]:
        """返回精确调用点的全部候选目标，不把空集合解释为无风险。"""
        ...


@runtime_checkable
class SemanticCallSiteView(SemanticIndexView, Protocol):
    """可选的位置敏感调用查询；旧基础查询实现不必提供占位方法。"""

    def callsite(self, callsite_id: str) -> CallSiteFact | None:
        """查询稳定调用身份，包括未解析事实。"""
        ...

    def calls_at(self, location: SemanticLocation, *, caller_symbol: str | None = None) -> Iterable[CallSiteFact]:
        """按已知坐标筛选调用，保留同一行、重载和缺少列号时的歧义。"""
        ...


@runtime_checkable
class SemanticTypeView(SemanticIndexView, Protocol):
    """可选的声明级类型查询，不要求旧前端伪造精确声明或使用点类型。"""

    def variable_types_for(self, owner_symbol: str, name: str, *, role: VariableRole | None = None) -> Iterable[VariableTypeFact]:
        """返回所属符号与名字对应的全部声明，不折叠同类型遮蔽。"""
        ...

    def variable_types_at(
        self, declaration_location: SemanticLocation, *, owner_symbol: str | None = None,
        name: str | None = None,
    ) -> Iterable[VariableTypeFact]:
        """按声明原始位置筛选，保留歧义，不按行号猜测使用点绑定。"""
        ...


@runtime_checkable
class SemanticReferenceView(SemanticIndexView, Protocol):
    """可选的源码变量引用绑定能力；没有原生事实的语言不伪造占位绑定。"""

    def variable_declaration(self, declaration_id: str) -> VariableDeclarationFact | None:
        """查询源码变量/形参声明实体。"""
        ...

    def variable_reference(self, reference_id: str) -> VariableReferenceFact | None:
        """查询引用及声明候选，未知原因原样保留。"""
        ...

    def variable_references_at(self, location: SemanticLocation, *, owner_symbol: str | None = None) -> Iterable[VariableReferenceFact]:
        """按引用位置查询，歧义不选择第一项。"""
        ...

    def variable_references_to(self, declaration_id: str) -> Iterable[VariableReferenceFact]:
        """反查声明引用，不等于到达定义分析。"""
        ...

    def declaration_types_for_reference(self, reference_id: str) -> Iterable[VariableTypeFact]:
        """返回声明类型候选，不声称已实现使用点流敏感类型。"""
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


@runtime_checkable
class SemanticSSAView(SemanticIndexView, Protocol):
    """可选的原生 SSA 查询能力，不强迫只有基础事实的语言实现 SSA。"""

    def native_ssa_for(self, symbol_id: str) -> SSAFunctionFact | None:
        """按现有符号查询原生事实；缺失不等于函数无数据流或安全。"""
        ...
