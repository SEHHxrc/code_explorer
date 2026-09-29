"""Python 调用实参与形参的基础绑定语义。"""

from __future__ import annotations

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite

from ..ir import IRCall, IRFunction
from .base import ArgumentBinding


class PythonLanguageSemantics:
    """处理不含 ``*args``/``**kwargs`` 展开的基础 Python 参数绑定。"""

    language = "python"

    def bind_arguments(
        self,
        call: IRCall,
        target: IRFunction,
    ) -> list[ArgumentBinding]:
        """按位置和显式关键字建立保守参数绑定。"""
        parameters = [
            parameter for parameter in target.parameters
            if parameter.name not in {"self", "cls"}
        ]
        bindings: list[ArgumentBinding] = []
        for index, _argument in enumerate(call.arguments):
            if index >= len(parameters):
                break
            bindings.append(ArgumentBinding(
                callsite_id=call.callsite_id,
                parameter_name=parameters[index].name,
                argument_position=index,
            ))
        parameter_names = {parameter.name for parameter in parameters}
        for keyword, _argument in call.keywords:
            if keyword in parameter_names:
                bindings.append(ArgumentBinding(
                    callsite_id=call.callsite_id,
                    parameter_name=keyword,
                    argument_keyword=keyword,
                ))
        return bindings

    def bind_program_arguments(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[ArgumentBinding]:
        """在 ProgramGraph 上绑定 Python 位置参数和名称明确的关键字参数。"""
        parameters = [name for name in target.parameters if name not in {"self", "cls"}]
        bindings = [
            ArgumentBinding(
                callsite_id=call.callsite_id,
                parameter_name=parameters[index],
                argument_position=index,
            )
            for index, _identifiers in enumerate(call.positional_arguments)
            if index < len(parameters)
        ]
        parameter_names = set(parameters)
        bindings.extend(
            ArgumentBinding(
                callsite_id=call.callsite_id,
                parameter_name=keyword,
                argument_keyword=keyword,
            )
            for keyword in call.keyword_arguments
            if keyword in parameter_names
        )
        return bindings
