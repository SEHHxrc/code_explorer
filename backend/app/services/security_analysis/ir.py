"""安全分析语言前端与规则引擎之间的内部中间表示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from backend.app.services.value_binding import BindingPattern, BindingValue
from backend.app.services.value_binding.parameters import ParameterKind


@dataclass(frozen=True)
class IRLocation:
    """语言前端观察到的一处项目相对源码位置。"""

    path: str
    line: int
    column: int
    end_line: int
    end_column: int
    location_id: str


@dataclass(frozen=True)
class IRExpression:
    """保留文本、标识符和静态常量形态的表达式。"""

    text: str
    identifiers: tuple[str, ...] = ()
    is_literal: bool = False
    literal: Any = None
    kind: str = "expression"
    contains_call: bool = False
    binding_value: BindingValue | None = None


@dataclass(frozen=True)
class IRParameter:
    """函数参数及其注解、默认调用和精确位置。"""

    name: str
    annotation: str
    default_call: str
    location: IRLocation
    binding_pattern: BindingPattern | None = None
    kind: ParameterKind = "positional_or_keyword"


@dataclass(frozen=True)
class IRDecorator:
    """函数装饰器及其有界参数表达式。"""

    qualified_name: str
    arguments: tuple[IRExpression, ...] = ()


@dataclass(frozen=True)
class IRFunction:
    """与依赖图 FQN 对齐的函数或方法。"""

    symbol: str
    name: str
    parameters: tuple[IRParameter, ...]
    decorators: tuple[IRDecorator, ...]
    location: IRLocation
    owner_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class IRCall:
    """调用表达式及未来数据流分析所需的参数角色信息。"""

    qualified_name: str
    callsite_id: str
    symbol: str
    location: IRLocation
    arguments: tuple[IRExpression, ...] = ()
    keywords: tuple[tuple[str, IRExpression], ...] = ()
    assigned_targets: tuple[str, ...] = ()
    receiver: str = ""
    visible_headers: tuple[str, ...] = ()
    callee_is_project_defined: bool = False
    unknown_keywords: bool = False

    def keyword(self, name: str) -> IRExpression | None:
        """按关键字名称返回调用参数。"""
        return next((value for key, value in self.keywords if key == name), None)


@dataclass(frozen=True)
class IRCondition:
    """条件或断言表达式，尚不表示已验证的控制流约束。"""

    kind: Literal["if", "assert"]
    symbol: str
    expression: IRExpression
    location: IRLocation


@dataclass(frozen=True)
class IRAccess:
    """无法自然表示为调用的读取或写入表达式。"""

    qualified_name: str
    symbol: str
    access_kind: Literal["read", "write"]
    location: IRLocation
    assigned_targets: tuple[str, ...] = ()
    value_identifiers: tuple[str, ...] = ()
    parameter_seed: str = ""
    binding_certainty: Literal["must", "may"] = "must"


@dataclass
class SecurityProgramIR:
    """单次语言前端扫描产生的项目级安全分析 IR。"""

    language: str
    functions: list[IRFunction] = field(default_factory=list)
    calls: list[IRCall] = field(default_factory=list)
    conditions: list[IRCondition] = field(default_factory=list)
    accesses: list[IRAccess] = field(default_factory=list)
    value_boundaries: list[IRValueBoundary] = field(default_factory=list)
    files_considered: int = 0
    files_scanned: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class IRValueBoundary:
    """有静态依据的跨函数值绑定，不是 Source、调用边或运行时可达证明。

    source_scope/target_scope: 精确函数范围，避免同名函数或变量跨作用域串联。
    source_location/source_names: 写入操作及该操作处待追踪的变量槽位。
    target_location/target_names: 读取侧定义点及槽位；capture 为 True 时是入口捕获。
    kind: 适配器提供的绑定类别，共享传播器不按框架分支。
    source_at_exit: 从公共 CFG 的退出值导出槽位，而非直接拼接某次中间写入。
    limitations: 框架语义的局限；所有此类连接均按 inferred/may 处理。
    """

    boundary_id: str
    kind: str
    source_scope: IRLocation
    source_location: IRLocation
    source_names: tuple[str, ...]
    target_scope: IRLocation
    target_location: IRLocation
    target_names: tuple[str, ...]
    capture: bool = False
    limitations: tuple[str, ...] = ()
    source_at_exit: bool = False
