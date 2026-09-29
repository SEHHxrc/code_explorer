"""Java 调用实参与形参的基础绑定语义。"""

from __future__ import annotations

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite

from ..ir import IRCall, IRFunction
from .base import ArgumentBinding


class JavaLanguageSemantics:
    """按 Java 位置参数建立保守绑定，不自行推断动态派发目标。"""

    language = "java"

    def bind_arguments(
        self,
        call: IRCall,
        target: IRFunction,
    ) -> list[ArgumentBinding]:
        """将位置实参映射到声明顺序中的形参。"""
        return [
            ArgumentBinding(
                callsite_id=call.callsite_id,
                parameter_name=target.parameters[index].name,
                argument_position=index,
            )
            for index, _argument in enumerate(call.arguments)
            if index < len(target.parameters)
        ]

    def bind_program_arguments(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[ArgumentBinding]:
        """在 ProgramGraph 上按 Java 位置参数顺序建立边界绑定。"""
        return [
            ArgumentBinding(
                callsite_id=call.callsite_id,
                parameter_name=target.parameters[index],
                argument_position=index,
            )
            for index, _identifiers in enumerate(call.positional_arguments)
            if index < len(target.parameters)
        ]
