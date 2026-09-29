"""多语言调用参数绑定语义接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite

from ..ir import IRCall, IRFunction


@dataclass(frozen=True)
class ArgumentBinding:
    """一个调用实参与目标函数形参的确定性或保守绑定。"""

    callsite_id: str
    parameter_name: str
    argument_position: int | None = None
    argument_keyword: str = ""
    confidence: Literal["high", "medium", "low"] = "high"


class LanguageSemantics(Protocol):
    """为通用数据流引擎提供语言特有的调用边界语义。"""

    language: str

    def bind_arguments(
        self,
        call: IRCall,
        target: IRFunction,
    ) -> list[ArgumentBinding]:
        """将调用实参映射到目标函数形参，不宣称实参本身受污染。"""
        ...

    def bind_program_arguments(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[ArgumentBinding]:
        """在公共 ProgramGraph 调用点上执行同一种语言绑定语义。"""
        ...
