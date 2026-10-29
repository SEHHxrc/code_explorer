"""安全传播的语言参数绑定；旧调用索引名称仅兼容导出同一公共实现。"""

from __future__ import annotations

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite
from backend.app.services.semantic_index import CallTransition, CallTransitionIndex
from backend.app.services.value_binding.access_paths import attribute_path
from backend.app.services.program_graph.conditions import ConditionExpression

from ..registry import SemanticsRegistry

__all__ = ["CallTransition", "CallTransitionIndex", "ProgramArgumentBinder"]


class ProgramArgumentBinder:
    """把 ProgramGraph 调用实参按注册语言语义绑定到目标形参。"""

    def __init__(self, semantics: SemanticsRegistry | None = None) -> None:
        """保存可选语言语义注册表；缺失时使用保守公共绑定。"""
        self.semantics = semantics

    def bind(
        self,
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[tuple[str, tuple[str, ...]]]:
        """返回目标形参与对应实参中词法变量的绑定。"""
        target = self._explicit_target(target)
        if call.argument_binding_error:
            return []
        context = target.instance_context
        if context is not None and not context.is_constructor and call.receiver_field_bindings is not None and target.method_id not in call.receiver_field_bindings:
            return []
        semantics = self.semantics.get(target.language) if self.semantics else None
        result: list[tuple[str, tuple[str, ...]]] = self._fallback(call, target) if semantics is None else []
        for binding in semantics.bind_program_arguments(call, target) if semantics else ():
            identifiers: list[str] = []
            if binding.source_identifiers is not None:
                identifiers = list(binding.source_identifiers)
            elif binding.argument_position is not None:
                position = binding.argument_position
                if 0 <= position < len(call.positional_arguments):
                    identifiers = call.positional_arguments[position]
            elif binding.argument_keyword:
                identifiers = call.keyword_arguments.get(binding.argument_keyword, [])
            names = tuple(dict.fromkeys(identifiers))
            if binding.source_identifiers is not None and binding.argument_position is not None:
                position = binding.argument_position
                if position < len(call.argument_values) and call.argument_values[position].kind == "opaque":
                    roots = call.argument_values[position].variables
                    names = tuple(dict.fromkeys(next((root for root in roots if name.startswith((root + ".", root + "["))), name) for name in names))
            if names:
                result.append((binding.parameter_name, names))
        if context is not None and not context.is_constructor and attribute_path(call.receiver) is not None:
            if call.receiver_field_bindings is not None:
                result.extend((name, (value,)) for name, value in call.receiver_field_bindings.get(target.method_id, {}).items())
            else:
                prefix = context.receiver + "."
                result.extend((name, (call.receiver + name[len(context.receiver):],))
                              for name in target.nodes[target.entry_node_id].definitions
                              if name.startswith(prefix) and name[len(prefix):].isidentifier())
        result.extend((name, (value,)) for name, value in call.argument_field_bindings.get(target.method_id, {}).items())
        return result

    def bind_references(self, call: ProgramCallSite, target: FunctionProgramGraph) -> list[tuple[str, str]]:
        """复用同一参数绑定器，只保留前端确认的直接引用实参；变换表达式与旧缺失事实不猜对象。

        不根据“表达式恰好含一个变量”推断别名；按参数位置/关键字分别过滤，避免同名变量在
        一个位置是直接引用、另一个位置是变换表达式时被一并放行。接收者另由实例上下文绑定。
        """
        direct = call.model_copy(update={
            "positional_arguments": [[name] if index < len(call.positional_references)
                and (name := call.positional_references[index]) and names == [name] else []
                for index, names in enumerate(call.positional_arguments)],
            "keyword_arguments": {key: [name] if (name := call.keyword_references.get(key)) and names == [name] else []
                for key, names in call.keyword_arguments.items()},
            "argument_values": [], "argument_field_bindings": {},
        })
        return [(parameter, names[0]) for parameter, names in self.bind(direct, target)
                if parameter.isidentifier() and len(names) == 1 and attribute_path(names[0]) is not None]

    def is_uncertain(self, call: ProgramCallSite, target: FunctionProgramGraph) -> bool:
        """未知映射展开只有条件性绑定；调用边本身确定也不能升级为 must 值流。"""
        semantics = self.semantics.get(target.language) if self.semantics else None
        return bool(semantics and any(item.confidence != "high"
            for item in semantics.bind_program_arguments(call, self._explicit_target(target))))

    def slot_names(self, call: ProgramCallSite, target: FunctionProgramGraph) -> set[str]:
        """返回包括安全常量的目标槽位，供私有入口 Overlay 保留兄弟元素隔离。"""
        target = self._explicit_target(target)
        semantics = self.semantics.get(target.language) if self.semantics else None
        return {binding.parameter_name for binding in semantics.bind_program_arguments(call, target)} if semantics else set()

    def bind_literals(self, call: ProgramCallSite, target: FunctionProgramGraph) -> list[tuple[str, ConditionExpression]]:
        """复用相同语言参数位置/关键字绑定，补充已观察字面量，不从形参顺序猜默认值。"""
        return [(name, expression) for name, expression in self.bind_conditions(call, target) if expression.kind == "literal"]

    def bind_conditions(self, call: ProgramCallSite, target: FunctionProgramGraph) -> list[tuple[str, ConditionExpression]]:
        """复用已有调用绑定，保留纯实参表达式，并仅对确实省略的参数应用已观察字面量默认值。"""
        target = self._explicit_target(target)
        semantics = self.semantics.get(target.language) if self.semantics else None
        if semantics is None:
            candidates = [(name, call.positional_conditions[index]) for index, name in enumerate(target.parameters)
                          if index < len(call.positional_conditions)] + [
                              (name, expression) for name, expression in call.keyword_conditions.items() if name in target.parameters]
        else:
            candidates = []
            for binding in semantics.bind_program_arguments(call, target):
                expression = (call.positional_conditions[binding.argument_position]
                    if binding.argument_position is not None and 0 <= binding.argument_position < len(call.positional_conditions)
                    else call.keyword_conditions.get(binding.argument_keyword))
                if expression is not None:
                    candidates.append((binding.parameter_name, expression))
        provided = self.slot_names(call, target) if semantics else set(target.parameters[:len(call.positional_arguments)]) | set(call.keyword_arguments)
        if not call.positional_spread_positions and all(name.isidentifier() for name in call.keyword_arguments):
            candidates.extend((name, expression) for name, expression in target.parameter_defaults.items() if name not in provided)
        return [(name, expression) for name, expression in candidates if expression.kind != "unknown"]

    @staticmethod
    def _explicit_target(target: FunctionProgramGraph) -> FunctionProgramGraph:
        """公共层排除语法适配器确认的隐式绑定接收者，不按语言或 self 拼写分支。"""
        context = target.instance_context
        if context is None or context.receiver not in target.parameters:
            return target
        return target.model_copy(update={"parameters": [name for name in target.parameters if name != context.receiver],
            "parameter_patterns": [pattern for name, pattern in zip(target.parameters, target.parameter_patterns) if name != context.receiver]})

    @staticmethod
    def _fallback(
        call: ProgramCallSite,
        target: FunctionProgramGraph,
    ) -> list[tuple[str, tuple[str, ...]]]:
        """未注册语言语义时仅绑定位置参数和名称完全一致的关键字参数。"""
        result: list[tuple[str, tuple[str, ...]]] = []
        for index, identifiers in enumerate(call.positional_arguments):
            if index >= len(target.parameters):
                break
            names = tuple(dict.fromkeys(identifiers))
            if names:
                result.append((target.parameters[index], names))
        parameter_names = set(target.parameters)
        for keyword, identifiers in call.keyword_arguments.items():
            names = tuple(dict.fromkeys(identifiers))
            if keyword in parameter_names and names:
                result.append((keyword, names))
        return result
