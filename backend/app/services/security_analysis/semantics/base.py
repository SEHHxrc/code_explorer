"""多语言调用参数绑定语义接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite

from ..ir import IRCall, IRFunction
from backend.app.services.value_binding.parameters import ParameterKind
from backend.app.services.value_binding import BindingValue


@dataclass(frozen=True)
class ArgumentBinding:
    """一个调用实参与目标函数形参的确定性或保守绑定。"""

    callsite_id: str
    parameter_name: str
    argument_position: int | None = None
    argument_keyword: str = ""
    confidence: Literal["high", "medium", "low"] = "high"
    source_identifiers: tuple[str, ...] | None = None


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


class PositionalLanguageSemantics:
    """复用固定参数与静态可变参数槽位绑定，不推断调用目标或堆副作用。"""

    language: str

    def bind_arguments(self, call: IRCall, target: IRFunction) -> list[ArgumentBinding]:
        """输入 IR 调用和目标声明，返回已有具名形参对应的位置实参绑定。"""
        return self._bind_values(
            call.callsite_id,
            [item.name for item in target.parameters],
            {item.name: item.kind for item in target.parameters},
            len(call.arguments),
            [
                index
                for index, item in enumerate(call.arguments)
                if item.kind in {"starred", "variadic_argument"}
            ],
            [
                item.binding_value or BindingValue(variables=item.identifiers)
                for item in call.arguments
            ],
            self.language,
        )

    def bind_program_arguments(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[ArgumentBinding]:
        """输入公共图调用点和目标函数，按相同声明顺序绑定位置实参。"""
        return self._bind_values(
            call.callsite_id,
            target.parameters,
            target.parameter_kinds,
            len(call.positional_arguments),
            call.positional_spread_positions,
            call.argument_values,
            target.language,
        )

    def _bind_values(
        self,
        callsite: str,
        parameters: list[str],
        kinds: dict[str, ParameterKind],
        count: int,
        spreads: list[int],
        values: list[BindingValue],
        language: str,
    ) -> list[ArgumentBinding]:
        """IR 与公共图共用相同固定前缀和打包序列投影，不重复实现各语言算法。"""
        limit = min(spreads, default=count)
        result = self._positional_bindings(callsite, parameters, kinds, limit)
        variadic = next(
            (
                index
                for index, name in enumerate(parameters)
                if kinds.get(name) == "variadic_positional"
            ),
            None,
        )
        if variadic is not None:
            packed = next(
                (
                    index
                    for index in spreads
                    if index < len(values) and values[index].kind == "array"
                ),
                None,
            )
            if (
                packed is None
                and language == "java"
                and count == variadic + 1
                and variadic < len(values)
                and values[variadic].kind == "array"
            ):
                packed = variadic
            if packed is not None and packed >= variadic:
                result = [
                    binding for binding in result if binding.argument_position != packed
                ]
                result.extend(
                    self._packed_bindings(
                        callsite,
                        parameters[variadic],
                        packed - variadic,
                        packed,
                        values[packed],
                    )
                )
        return result

    @staticmethod
    def _packed_bindings(
        callsite: str, parameter: str, offset: int, argument: int, value: BindingValue
    ) -> list[ArgumentBinding]:
        """相同序列协议把已知打包数组成员映射到可变参数槽位，保留固定常量空输入。"""
        return [
            ArgumentBinding(
                callsite,
                f"{parameter}[{offset + member.selector}]",
                argument_position=argument,
                source_identifiers=member.value.variables,
            )
            for member in value.members
            if isinstance(member.selector, int)
        ]

    @staticmethod
    def _positional_bindings(
        callsite: str,
        parameters: list[str],
        kinds: dict[str, ParameterKind],
        count: int,
    ) -> list[ArgumentBinding]:
        """返回固定位置及可变序列的常量元素槽位，不将整个序列污染到安全兄弟元素。"""
        result: list[ArgumentBinding] = []
        position = 0
        for parameter in parameters:
            kind = kinds.get(parameter, "positional_or_keyword")
            if kind in {"keyword_only", "variadic_keyword"}:
                continue
            if kind == "variadic_positional":
                result.extend(
                    ArgumentBinding(
                        callsite,
                        f"{parameter}[{index - position}]",
                        argument_position=index,
                    )
                    for index in range(position, count)
                )
                break
            if position >= count:
                break
            result.append(
                ArgumentBinding(callsite, parameter, argument_position=position)
            )
            position += 1
        return result
