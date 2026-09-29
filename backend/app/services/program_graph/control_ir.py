"""语言前端与通用 CFG Pass 之间的结构化控制 IR。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .contracts import EdgeCertainty, ProgramGraphLocation, ValueTransferKind

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


@dataclass(frozen=True)
class ControlValueTransfer:
    """语言前端直接观察到的一项节点内输入到输出关系。"""

    output_variable: str
    input_variables: tuple[str, ...]
    transfer_kind: ValueTransferKind
    callsite_ids: tuple[str, ...] = ()
    certainty: EdgeCertainty = "must"
    provenance: Literal["observed", "inferred"] = "observed"


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


@dataclass
class FrontendResult:
    """一种语言前端的函数、覆盖率和失败诊断。"""

    language: str
    functions: list[ControlFunction] = field(default_factory=list)
    files_considered: int = 0
    files_parsed: int = 0
    failures: list[dict[str, object]] = field(default_factory=list)
