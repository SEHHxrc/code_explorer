"""Go 调用实参与形参的基础绑定语义。"""

from __future__ import annotations

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite

from ..ir import IRCall, IRFunction
from .base import ArgumentBinding


class GoLanguageSemantics:
    """按 Go 声明顺序绑定位置参数，不推断接口动态派发或多返回值。"""

    language = "go"

    def bind_arguments(
        self,
        call: IRCall,
        target: IRFunction,
    ) -> list[ArgumentBinding]:
        """将普通位置实参映射到目标函数参数。"""
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
        """在 ProgramGraph 调用点上执行相同的位置参数绑定。"""
        return [
            ArgumentBinding(
                callsite_id=call.callsite_id,
                parameter_name=target.parameters[index],
                argument_position=index,
            )
            for index, _identifiers in enumerate(call.positional_arguments)
            if index < len(target.parameters)
        ]
