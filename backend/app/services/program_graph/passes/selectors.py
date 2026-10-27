"""公共 CFG 上的有界标量键求值和访问路径投影；不是污点求解器，不执行表达式。"""

from collections import defaultdict, deque
from dataclasses import replace
from itertools import product
from functools import lru_cache

from backend.app.services.value_binding.access_paths import AccessPath, AccessPathSegment, canonical_path
from backend.app.services.value_binding.identities import value_identity
from backend.app.services.value_binding.selectors import SelectorExpression
from backend.app.services.value_binding import BindingPattern, BindingValue
from ..contracts import FunctionProgramGraph, ProgramGraphNode, ProgramValueTransfer

ConstantSet = frozenset[tuple[str, str | int]] | None


def _join(values: list[ConstantSet], limit: int) -> ConstantSet:
    """合并有限值；任一未知分支或超限必须保留未知，不能挑已知分支作精确事实。"""
    if not values or any(value is None for value in values):
        return None
    result = frozenset().union(*(value for value in values if value is not None))
    return result if len(result) <= limit else None


class SelectorResolutionPass:
    """各语言共用简单键求值；只支持常量/名称/有限选择，复杂算术和调用仍为未知。"""

    def __init__(self, *, max_values: int = 8, max_updates: int = 100000) -> None:
        """设置有限候选数与工作列表预算；不共享跨项目可变状态。"""
        if not 1 <= max_values <= 8 or max_updates < 1:
            raise ValueError("Selector budgets must be positive and candidate count at most 8")
        self.max_values, self.max_updates = max_values, max_updates

    def _evaluate(self, expression: SelectorExpression, node: ProgramGraphNode,
                  environment: dict[str, ConstantSet]) -> ConstantSet:
        """返回类型隔离的常量集合；缺失定义、未知或未支持表达式返回 None。"""
        if expression.kind == "literal" and expression.value is not None:
            return frozenset({(type(expression.value).__name__, expression.value)})
        if expression.kind == "name":
            return environment.get(value_identity(node, expression.name, write=False))
        if expression.kind == "choice":
            return _join([self._evaluate(choice, node, environment) for choice in expression.choices], self.max_values)
        return None

    def apply(self, function: FunctionProgramGraph) -> FunctionProgramGraph:
        """按操作前环境投影键；唯一键精确、多键弱写、未知键转为独立摘要选择器。"""
        if not any(node.access_selectors for node in function.nodes.values()):
            # 没有动态访问的函数不保存用不到的键常量事实，避免扩大大型项目的持久化产物。
            nodes = {key: node.model_copy(update={"selector_definitions": {}}) if node.selector_definitions else node
                     for key, node in function.nodes.items()}
            return function.model_copy(update={"nodes": nodes}) if nodes != function.nodes else function
        incoming_edges, successors = defaultdict(list), defaultdict(set)
        for edge in function.edges:
            if edge.kind == "cfg":
                incoming_edges[edge.target].append(edge)
                successors[edge.source].add(edge.target)
        before, after, initialized = {}, {}, set()
        pending, queued = deque([function.entry_node_id]), {function.entry_node_id}
        updates, entries, exceeded = 0, 0, False
        while pending:
            key = pending.popleft()
            queued.discard(key)
            environments = [(before[edge.source] if edge.source_before_write else after[edge.source])
                            for edge in incoming_edges[key] if edge.source in initialized]
            keys = set().union(*environments) if environments else set()
            incoming = {name: _join([environment.get(name) for environment in environments], self.max_values) for name in keys}
            node, outgoing = function.nodes[key], dict(incoming)
            for name in node.definitions:
                if not name.isidentifier():
                    continue  # 对象字段不是可随便传播为键常量的标量声明。
                value = self._evaluate(node.selector_definitions.get(name, SelectorExpression()), node, incoming)
                identity = value_identity(node, name, write=True)
                outgoing[identity] = _join([incoming.get(identity), value], self.max_values) if node.certainty == "may" else value
            if key in initialized and before[key] == incoming and after[key] == outgoing:
                continue
            entries += len(incoming) + len(outgoing) - len(before.get(key, {})) - len(after.get(key, {}))
            before[key], after[key] = incoming, outgoing
            initialized.add(key)
            updates += 1
            if updates > self.max_updates or entries > 100000:
                exceeded = True
                break
            for child in sorted(successors[key]):
                if child not in queued:
                    queued.add(child)
                    pending.append(child)
        nodes = {}
        for node in function.nodes.values():
            values = {key: None if exceeded else self._evaluate(expression, node, before.get(node.node_id, {}))
                      for key, expression in node.access_selectors.items()}
            nodes[node.node_id] = self._project(node, values, exceeded)
        return function.model_copy(update={"nodes": nodes, "limitations": list(dict.fromkeys((*function.limitations,
            "集合键仅按局部常量/有限选择有界求值；未知键按 may 元素读写，未证明完整键集合、对象身份或运行可达。")))})

    def _project(self, node: ProgramGraphNode, values: dict[str, ConstantSet], exceeded: bool) -> ProgramGraphNode:
        """同步节点、调用和返回契约；键本身仍是求值输入，不能当成被选元素的污点内容。"""
        if not values:
            return node

        @lru_cache(maxsize=512)
        def names(name: str) -> tuple[str, ...]:
            """将一个规范访问投影为有界候选；多段乘积超限整体退为未知选择。"""
            path = canonical_path(name, selectors=True)
            if path is None or not any(segment.kind == "selector" for segment in path.segments):
                return (name,)
            choices = []
            for segment in path.segments:
                if segment.kind != "selector":
                    choices.append((segment,))
                    continue
                constants = values.get(str(segment.value))
                choices.append(tuple(AccessPathSegment("index", value) for _, value in sorted(constants))
                               if constants else (AccessPathSegment("unknown", ""),))
            count = 1
            for choice in choices:
                count *= len(choice)
            if count > self.max_values:
                choices = [(AccessPathSegment("unknown", ""),) if segment.kind == "selector" else (segment,)
                           for segment in path.segments]
            return tuple(AccessPath(path.root, tuple(parts)).render() for parts in product(*choices))

        def many(items: list[str] | tuple[str, ...]) -> list[str]:
            """稳定去重，避免多候选键把同一输入重复加入工具/边界。"""
            return list(dict.fromkeys(value for item in items for value in names(item)))

        def reference(name: str) -> str:
            """只有一个精确路径可以保留引用身份，多个可能来源不假装唯一对象。"""
            result = names(name)
            return result[0] if len(result) == 1 and "[?]" not in result[0] else ""

        def binding(value: BindingValue) -> BindingValue:
            """复用既有 BindingValue 的成员结构，不创建另一套返回/解构规则。"""
            return replace(value, variables=tuple(many(value.variables)), members=tuple(
                replace(member, value=binding(member.value)) for member in value.members))

        def pattern(value: BindingPattern | None) -> BindingPattern | None:
            """同步唯一返回接收路径；多目的键不伪装成解包 ABI，明确保留返回绑定前沿。"""
            if value is None:
                return None
            outputs = names(value.name) if value.name else ()
            if value.name and (len(outputs) != 1 or "[?]" in outputs[0]):
                return BindingPattern(kind="unknown", reason="selector_return_receiver_not_modelled")
            return replace(value, name=outputs[0] if outputs else value.name, members=tuple(
                replace(member, pattern=pattern(member.pattern)) for member in value.members))

        transfers = [transfer.model_copy(update={"output_variable": output,
            "input_variables": many(transfer.input_variables), "reference_source": reference(transfer.reference_source),
            "weak_update": transfer.weak_update or len(names(transfer.output_variable)) > 1 or "[?]" in output,
            "certainty": "may" if len(names(transfer.output_variable)) > 1 or "[?]" in output else transfer.certainty})
            for transfer in node.value_transfers for output in names(transfer.output_variable)]
        weak = any(len(names(name)) > 1 or any("[?]" in value for value in names(name)) for name in node.definitions)
        if weak:
            for name in node.definitions:
                for output in names(name):
                    if (len(names(name)) > 1 or "[?]" in output) and not any(item.output_variable == output for item in transfers):
                        transfers.append(ProgramValueTransfer(output_variable=output, transfer_kind="assignment", certainty="may", weak_update=True))
        calls = [call.model_copy(update={
            "positional_arguments": [many(items) for items in call.positional_arguments],
            "keyword_arguments": {key: many(items) for key, items in call.keyword_arguments.items()},
            "receiver_identifiers": many(call.receiver_identifiers),
            "positional_references": [reference(name) for name in call.positional_references],
            "keyword_references": {key: reference(name) for key, name in call.keyword_references.items()},
            "argument_values": [binding(value) for value in call.argument_values],
            "result_targets": [names(name)[0] if len(names(name)) == 1 else name for name in call.result_targets],
            "result_variable": reference(call.result_variable),
            "result_pattern": pattern(call.result_pattern),
        }) for call in node.calls]
        report = node.return_binding
        if report is not None:
            report = replace(report, values=tuple(binding(value) for value in report.values))
        diagnostics = [*node.selector_diagnostics]
        if exceeded:
            diagnostics.append("selector_budget_exceeded")
        if any(value is None for value in values.values()):
            diagnostics.append("selector_unknown")
        if any(value is not None and len(value) > 1 for value in values.values()):
            diagnostics.append("selector_finite_alternatives")
        return node.model_copy(update={"definitions": many(node.definitions), "uses": many(node.uses),
            "value_transfers": transfers, "calls": calls, "return_values": [many(part) for part in node.return_values],
            "return_binding": report,
            "selector_diagnostics": list(dict.fromkeys(diagnostics))})
