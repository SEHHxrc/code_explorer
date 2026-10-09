"""JS/TS 位置参数基础语义；不假设 Promise、回调或堆对象副作用已被建模。"""

from ..ir import IRCall, IRFunction
from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite
from backend.app.services.value_binding import (
    BindingPattern,
    BindingValue,
    StructuredBindingResolver,
)
from .base import ArgumentBinding, PositionalLanguageSemantics


class JavaScriptLanguageSemantics(PositionalLanguageSemantics):
    """对具名普通参数按位置绑定；复杂 pattern 由前端诊断。"""

    language = "javascript"

    def bind_arguments(self, call: IRCall, target: IRFunction) -> list[ArgumentBinding]:
        """只绑定 spread 之前的普通实参和简单参数，不猜测动态解构/可变参数。"""
        spread = next(
            (
                index
                for index, value in enumerate(call.arguments)
                if value.kind == "spread_element"
            ),
            len(call.arguments),
        )
        return [
            binding
            for index, parameter in enumerate(target.parameters[:spread])
            if index < len(call.arguments)
            if not parameter.binding_pattern or parameter.binding_pattern.kind != "rest"
            for binding in self._project(
                call.callsite_id,
                index,
                parameter.binding_pattern
                or BindingPattern("name", name=parameter.name),
                call.arguments[index].binding_value
                or BindingValue(variables=call.arguments[index].identifiers),
            )
        ]

    def bind_program_arguments(
        self, call: ProgramCallSite, target: FunctionProgramGraph
    ) -> list[ArgumentBinding]:
        """复用同一投影绑定函数级 DFG；固定安全字段的空输入不得回退成整个实参。"""
        return [
            binding
            for index, parameter in enumerate(target.parameters)
            if index < len(call.positional_arguments)
            if index >= len(target.parameter_patterns)
            or target.parameter_patterns[index].kind != "rest"
            for binding in self._project(
                call.callsite_id,
                index,
                target.parameter_patterns[index]
                if index < len(target.parameter_patterns)
                else BindingPattern("name", name=parameter),
                call.argument_values[index]
                if index < len(call.argument_values)
                else BindingValue(variables=tuple(call.positional_arguments[index])),
            )
        ]

    @staticmethod
    def _project(
        callsite: str, index: int, pattern: BindingPattern, value: BindingValue
    ) -> list[ArgumentBinding]:
        """公共结构化绑定到调用语义的薄适配，不自行实现对象/数组投影算法。"""
        return [
            ArgumentBinding(
                callsite,
                item.output,
                index,
                confidence="medium" if item.certainty == "may" else "high",
                source_identifiers=item.inputs,
            )
            for item in StructuredBindingResolver().resolve(pattern, value).projections
        ]


class TypeScriptLanguageSemantics(JavaScriptLanguageSemantics):
    """TS 类型擦除后复用相同运行时参数绑定。"""

    language = "typescript"
