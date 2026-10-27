"""语言前端与通用 CFG Pass 之间的结构化控制 IR。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.app.services.value_binding import BindingPattern, BindingValue
from backend.app.services.value_binding.parameters import ParameterKind
from backend.app.services.value_binding.returns import ReturnBinding
from backend.app.services.value_binding.storage import StorageAllocation
from backend.app.services.value_binding.collections import CollectionAllocation
from backend.app.services.value_binding.selectors import SelectorExpression

from .contracts import EdgeCertainty, InstanceMethodContext, ObjectAssignmentKind, ProgramGraphLocation, ReturnValueForm, ValueTransferKind
from .conditions import ConditionExpression

ControlStatementKind = Literal[
    "operation", "assignment", "call", "if", "loop", "return",
    "throw", "break", "continue", "try",
]


@dataclass(frozen=True)
class ControlCallSite:
    """语言前端提取的调用点及参数标识符。"""

    callsite_id: str
    name: str
    receiver: str
    location: ProgramGraphLocation
    positional_arguments: tuple[tuple[str, ...], ...] = ()
    keyword_arguments: tuple[tuple[str, tuple[str, ...]], ...] = ()
    receiver_identifiers: tuple[str, ...] = ()
    argument_values: tuple[BindingValue, ...] = ()
    positional_spread_positions: tuple[int, ...] = ()
    result_targets: tuple[str, ...] = ()  # 保留分量位置；空串表示前端明确观察到的丢弃槽。
    positional_argument_locations: tuple[ProgramGraphLocation, ...] = ()
    keyword_argument_locations: tuple[tuple[str, ProgramGraphLocation], ...] = ()
    positional_references: tuple[str, ...] = ()
    addressed_argument_positions: tuple[int, ...] = ()
    keyword_references: tuple[tuple[str, str], ...] = ()
    result_pattern: BindingPattern | None = None
    initializer_kind: Literal["", "base", "same"] = ""
    positional_conditions: tuple[ConditionExpression, ...] = ()
    keyword_conditions: tuple[tuple[str, ConditionExpression], ...] = ()


@dataclass(frozen=True)
class ControlValueTransfer:
    """语言前端直接观察到的一项节点内输入到输出关系。"""

    output_variable: str
    input_variables: tuple[str, ...]
    transfer_kind: ValueTransferKind
    callsite_ids: tuple[str, ...] = ()
    certainty: EdgeCertainty = "must"
    provenance: Literal["observed", "inferred"] = "observed"
    reference_source: str = ""
    storage_mode: Literal["", "reference", "address", "value_copy"] = ""


@dataclass(frozen=True)
class ControlStatement:
    """已由语言前端规范化的一条操作或结构化控制语句。"""

    statement_id: str
    kind: ControlStatementKind
    location: ProgramGraphLocation
    code: str
    definitions: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    calls: tuple[ControlCallSite, ...] = ()
    value_transfers: tuple[ControlValueTransfer, ...] = ()
    body: tuple[ControlStatement, ...] = ()
    alternative: tuple[ControlStatement, ...] = ()
    handlers: tuple[tuple[ControlStatement, ...], ...] = ()
    finalizer: tuple[ControlStatement, ...] = ()
    certainty: EdgeCertainty = "must"
    provenance: Literal["observed", "inferred", "generated"] = "observed"
    return_values: tuple[tuple[str, ...], ...] = ()
    return_value_locations: tuple[ProgramGraphLocation, ...] = ()
    return_value_forms: tuple[ReturnValueForm, ...] = ()
    return_binding: ReturnBinding | None = None
    storage_allocations: tuple[tuple[str, StorageAllocation], ...] = ()
    collection_allocations: tuple[tuple[str, CollectionAllocation], ...] = ()
    selector_definitions: tuple[tuple[str, SelectorExpression], ...] = ()
    access_selectors: tuple[tuple[str, SelectorExpression], ...] = ()
    condition: ConditionExpression | None = None
    scalar_values: tuple[tuple[str, ConditionExpression], ...] = ()
    handler_catch_all: tuple[bool, ...] = ()


@dataclass(frozen=True)
class ControlFunction:
    """一个函数的公共控制 IR；不包含语言专用 AST 对象。"""

    method_id: str
    symbol_id: str
    language: str
    name: str
    location: ProgramGraphLocation
    parameters: tuple[str, ...]
    body: tuple[ControlStatement, ...]
    limitations: tuple[str, ...] = ()
    parameter_patterns: tuple[BindingPattern, ...] = ()
    parameter_kinds: dict[str, ParameterKind] = field(default_factory=dict)
    instance_context: InstanceMethodContext | None = None
    deferred_return: bool = False
    object_assignment: ObjectAssignmentKind = "unknown"
    normal_exit_writes_modelled: bool = False
    has_body: bool = True
    storage_parameters: dict[str, Literal["value", "reference"]] = field(default_factory=dict)
    parameter_defaults: dict[str, ConditionExpression] = field(default_factory=dict)


@dataclass
class FrontendResult:
    """一种语言前端的函数、覆盖率和失败诊断。"""

    language: str
    functions: list[ControlFunction] = field(default_factory=list)
    files_considered: int = 0
    files_parsed: int = 0
    failures: list[dict[str, object]] = field(default_factory=list)
