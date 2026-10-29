"""Python 调用参数的静态绑定；动态展开不猜测长度和键。"""

from __future__ import annotations

import json

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite
from backend.app.services.value_binding.parameters import ParameterKind, mapping_member_bindings

from ..ir import IRCall, IRFunction
from .base import ArgumentBinding, PositionalLanguageSemantics


class PythonLanguageSemantics(PositionalLanguageSemantics):
    """位置/仅关键字参数及 args/kwargs 常量槽位；不模拟完整容器别名。"""

    language = "python"

    def bind_arguments(
        self,
        call: IRCall,
        target: IRFunction,
    ) -> list[ArgumentBinding]:
        """输入安全 IR 调用/声明，返回静态可确认的参数绑定。"""
        names = [
            item.name for item in target.parameters if item.name not in {"self", "cls"}
        ]
        kinds: dict[str, ParameterKind] = {
            item.name: item.kind for item in target.parameters
        }
        limit = next(
            (
                index
                for index, value in enumerate(call.arguments)
                if value.kind == "starred"
            ),
            len(call.arguments),
        )
        return self._bind(
            call.callsite_id, names, kinds, limit, [name for name, _ in call.keywords]
        )

    def bind_program_arguments(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[ArgumentBinding]:
        """输入公共图调用/声明；字面量展开由语法前端完成，旧产物保持普通绑定。"""
        if call.argument_binding_error:
            return []
        names = [name for name in target.parameters if name not in {"self", "cls"}]
        limit = min(
            call.positional_spread_positions, default=len(call.positional_arguments)
        )
        result = self._bind(
            call.callsite_id,
            names,
            target.parameter_kinds,
            limit,
            list(call.keyword_arguments),
        )
        if any(binding.parameter_name in call.keyword_arguments and target.parameter_kinds.get(binding.parameter_name) != "positional_only" for binding in
               self._positional_bindings(call.callsite_id, names, target.parameter_kinds, limit)):
            return []
        explicit = {item.parameter_name for item in result}
        roots = [call.keyword_references[key] for key in call.keyword_arguments
                 if key.startswith("**") and call.keyword_references.get(key)]
        parameters = {name: target.parameter_kinds.get(name, "positional_or_keyword") for name in names}
        result.extend(ArgumentBinding(call.callsite_id, name, confidence="medium", source_identifiers=(source,))
                      for name, source in mapping_member_bindings(parameters, roots, {item.parameter_name for item in result}))
        for extra in (name for name, kind in parameters.items() if roots and kind == "variadic_keyword"):
            demands = {name for node in target.nodes.values() for name in node.uses if name.startswith(extra + "[")}
            if demands:
                result = [item for item in result if item.parameter_name != extra or item.confidence == "high"]
            for name in sorted(demands)[:128]:
                if any(name == supplied or name.startswith((supplied + "[", supplied + ".")) for supplied in explicit):
                    continue
                result.extend(ArgumentBinding(call.callsite_id, name, confidence="medium",
                    source_identifiers=(root + name[len(extra):],)) for root in roots)
        return result

    @classmethod
    def _bind(
        cls,
        callsite: str,
        names: list[str],
        kinds: dict[str, ParameterKind],
        count: int,
        keywords: list[str],
    ) -> list[ArgumentBinding]:
        """共同绑定实现；仅关键字参数不消耗位置，positional-only 不接受同名关键字。"""
        result = cls._positional_bindings(callsite, names, kinds, count)
        provided = {binding.parameter_name for binding in result}
        if any(keyword in provided and kinds.get(keyword) != "positional_only" for keyword in keywords if not keyword.startswith("**")):
            return []
        extra = next(
            (name for name in names if kinds.get(name) == "variadic_keyword"), ""
        )
        for keyword in keywords:
            if keyword.startswith("**"):
                continue
            kind = kinds.get(keyword, "positional_or_keyword")
            if keyword in names and kind in {"positional_or_keyword", "keyword_only"}:
                parameter = keyword
            elif extra:
                parameter = f"{extra}[{json.dumps(keyword, ensure_ascii=False)}]"
            else:
                continue
            result.append(
                ArgumentBinding(callsite, parameter, argument_keyword=keyword)
            )
        return result
