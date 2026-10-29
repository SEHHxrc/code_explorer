"""基于公共 CFG/声明事实的有界可调用值解析；不执行反射，不按同名方法补边。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_graph.contracts import ProgramGraphArtifact, FunctionProgramGraph
from backend.app.services.program_graph.passes import ReachingDefinitionsPass
from backend.app.services.value_binding.contracts import CallableAccess
from backend.app.services.value_binding.expressions import ConditionExpression

from .contracts import CallSiteFact, ResolvedTargetFact, SemanticIndexArtifact, SemanticLocation
from .hierarchy import TypeHierarchy
from .index import SemanticIndex


class BoundedCallableResolver:
    """仅连接有接收者类型依据的成员可调用值；来源、成员、目标超限均保留诊断。"""

    def __init__(self, *, max_targets: int = 128, max_binding_depth: int = 8) -> None:
        """设置每调用点的候选数和局部别名回溯深度；不扩大成全项目同名搜索。"""
        if not 1 <= max_targets <= 512 or not 1 <= max_binding_depth <= 32:
            raise ValueError("动态调用预算超出允许范围")
        self.max_targets, self.max_binding_depth = max_targets, max_binding_depth

    def enrich(self, artifact: SemanticIndexArtifact, graph: ProgramGraphArtifact) -> tuple[SemanticIndexArtifact, ProgramGraphArtifact]:
        """读取规范成员访问、到达定义和类型层次，返回新调用事实及已绑定接收者的私有图。"""
        self.index = SemanticIndex(artifact)
        self.hierarchy = TypeHierarchy(artifact.types)
        self.members: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for function in graph.functions.values():
            if function.instance_context and function.has_body:
                self.members[function.name][function.instance_context.owner_symbol].append(function.symbol_id)
        self.type_names: dict[str, list[str]] = defaultdict(list)
        for symbol in artifact.types:
            self.type_names[symbol.rsplit("::", 1)[-1]].append(symbol)
        callsites, functions = dict(artifact.callsites), dict(graph.functions)
        for function in graph.functions.values():
            defined = {name for node in function.nodes.values() for name in node.definitions}
            candidates = [(node, call) for node in function.nodes.values() for call in node.calls
                          if not call.receiver and call.name in defined and call.name not in function.parameters
                          and not (callsites.get(call.callsite_id) and callsites[call.callsite_id].targets)]
            candidate_ids = {call.callsite_id for _, call in candidates}
            # 只为局部可调用值建立临时读边，不把 callee 当作实参污点或改写原值流。
            nodes = dict(function.nodes)
            for node, call in candidates:
                nodes[node.node_id] = nodes[node.node_id].model_copy(update={
                    "uses": sorted(set(nodes[node.node_id].uses) | {call.name})})
            if candidates:
                receiver_names = {call.callable_access.receiver for node in function.nodes.values()
                                  for call in node.calls if call.callable_access}
                receiver_names.update(item.reference_source.rsplit(".", 1)[0] for node in function.nodes.values()
                    for item in node.value_transfers if "." in item.reference_source and "[" not in item.reference_source)
                for node, _ in candidates:
                    nodes[node.node_id] = nodes[node.node_id].model_copy(update={
                        "uses": sorted(set(nodes[node.node_id].uses) | receiver_names)})
                for node in function.nodes.values():
                    receivers = {item.reference_source.rsplit(".", 1)[0] for item in node.value_transfers
                                 if "." in item.reference_source and "[" not in item.reference_source}
                    if receivers:
                        nodes[node.node_id] = nodes[node.node_id].model_copy(update={
                            "uses": sorted(set(nodes[node.node_id].uses) | receivers)})
            view = ReachingDefinitionsPass().apply(function.model_copy(update={"nodes": nodes})) if candidates else function
            self.incoming: dict[tuple[str, str], list[str]] = defaultdict(list)
            for edge in view.edges:
                if edge.kind == "reaching_def":
                    self.incoming[(edge.target, edge.variable)].append(edge.source)
            updated = dict(function.nodes)
            for node in function.nodes.values():
                for call in node.calls:
                    notes = []
                    if call.argument_binding_error:
                        notes.append(call.argument_binding_error)
                    if any(key.startswith("**") for key in call.keyword_arguments):
                        notes.append("mapping_keys_or_duplicates_unknown")
                    old = callsites.get(call.callsite_id) or CallSiteFact(
                        callsite_id=call.callsite_id, caller_symbol=function.symbol_id, language=function.language,
                        name=call.name, receiver=call.receiver, location=SemanticLocation(**call.location.model_dump()))
                    targets = old.targets
                    receiver = call.receiver
                    if call.callsite_id in candidate_ids:
                        accesses = self._accesses(view, node.node_id, call.name, set())
                        found: set[str] = set()
                        receivers = {access.receiver for access, _ in accesses}
                        if len(receivers) == 1:
                            receiver = next(iter(receivers))
                            for access, at in accesses:
                                names = self._member_names(view, at, access.member)
                                if not names:
                                    notes.append("dynamic_member_name_unknown")
                                if not access.receiver:
                                    for name in names:
                                        found.update(self._free_functions(view, name))
                                    continue
                                types = self._receiver_types(view, at, access.receiver, set())
                                if not types:
                                    notes.append("dynamic_receiver_type_unknown")
                                for name in names:
                                    declaring = self.members.get(name, {})
                                    for owner in types:
                                        for selected in self.hierarchy.member_owners(owner, set(declaring)):
                                            found.update(declaring[selected])
                                if set(self.incoming.get((at, access.receiver), [])) != set(self.incoming.get((node.node_id, access.receiver), [])):
                                    receiver = ""  # bound method 捕获旧对象，不能按变量新值绑定 self 字段。
                                    notes.append("dynamic_bound_receiver_changed")
                        else:
                            notes.append("dynamic_callable_binding_unknown")
                        truncated = len(found) > self.max_targets
                        if truncated:
                            notes.append("dynamic_target_limit")
                        if found:
                            targets = [ResolvedTargetFact(edge_id="dynamic-call:" + hashlib.sha256(
                                (call.callsite_id + "\x1f" + target).encode()).hexdigest()[:24], target_symbol=target,
                                relation="calls", dispatch="dynamic", resolution_method="bounded_callable_value",
                                certainty="may", confidence="medium", truncated=truncated)
                                for target in sorted(found)[:self.max_targets]]
                            current = updated[node.node_id]
                            updated[node.node_id] = current.model_copy(update={"calls": [
                                item.model_copy(update={"receiver": receiver, "receiver_identifiers": [receiver] if receiver else []})
                                if item.callsite_id == call.callsite_id else item for item in current.calls]})
                    callsites[call.callsite_id] = old.model_copy(update={"targets": targets,
                        "binding_diagnostics": list(dict.fromkeys([*old.binding_diagnostics, *notes])),
                        "unresolved_reason": None if targets else old.unresolved_reason,
                        "truncated": old.truncated or any(target.truncated for target in targets)})
            if updated != function.nodes:
                functions[function.method_id] = function.model_copy(update={"nodes": updated})
        return artifact.model_copy(update={"callsites": callsites}), graph.model_copy(update={"functions": functions})

    def _accesses(self, function: FunctionProgramGraph, at: str, name: str,
                  seen: set[tuple[str, str]]) -> list[tuple[CallableAccess, str]]:
        """沿实际到达定义回溯 callable 别名；覆盖、分支和未知写入不会跳过。"""
        key = (at, name)
        if key in seen or len(seen) >= self.max_binding_depth:
            return []
        result = []
        for source in self.incoming.get(key, []):
            node = function.nodes[source]
            for call in node.calls:
                if call.callable_access and (call.result_pattern and call.result_pattern.name == name):
                    # getattr 等访问器被形参、局部或项目函数遮蔽时不能套用内置语义。
                    fact = self.index.callsite(call.callsite_id)
                    if call.name not in function.parameters and not any(call.name in item.definitions for item in function.nodes.values()) \
                            and not (fact and any(not target.target_symbol.startswith("<") for target in fact.targets)):
                        result.append((call.callable_access, source))
            for transfer in node.value_transfers:
                if transfer.output_variable != name or not transfer.reference_source:
                    continue
                reference = transfer.reference_source
                if "." in reference and "[" not in reference:
                    receiver, member = reference.rsplit(".", 1)
                    result.append((CallableAccess(receiver, ConditionExpression(kind="literal", value=member)), source))
                elif reference.isidentifier():
                    if self._free_functions(function, reference):
                        result.append((CallableAccess("", ConditionExpression(kind="literal", value=reference)), source))
                    else:
                        result.extend(self._accesses(function, source, reference, seen | {key}))
        return result

    def _free_functions(self, function: FunctionProgramGraph, name: str) -> set[str]:
        """局部未遮蔽的精确模块函数值；不跨文件按短名或全局同名集合猜测。"""
        if name in function.parameters or any(name in node.definitions for node in function.nodes.values()):
            return set()
        symbol = function.location.path + "::" + name
        fact = self.index.callable(symbol)
        return {symbol} if fact and fact.kind == "function" else set()

    def _member_names(self, function: FunctionProgramGraph, at: str, expression: ConditionExpression) -> set[str]:
        """仅接受字面量或到达定义中的有限字符串；HTTP 参数等运行时名称保持未知。"""
        if expression.kind == "literal":
            return {expression.value} if isinstance(expression.value, str) and expression.value.isidentifier() else set()
        if expression.kind != "name":
            return set()
        definitions = self.incoming.get((at, expression.name), [])
        values = [function.nodes[node].scalar_values.get(expression.name) for node in definitions]
        if not values or len(values) > self.max_targets or any(value is None or value.kind != "literal" for value in values):
            return set()
        return {value.value for value in values if value and isinstance(value.value, str) and value.value.isidentifier()}

    def _receiver_types(self, function: FunctionProgramGraph, at: str, name: str,
                        seen: set[tuple[str, str]]) -> set[str]:
        """优先读取对象定义处的实例化/返回类型，拒绝跨越覆盖使用旧变量名类型。"""
        key = (at, name)
        if key in seen or len(seen) >= self.max_binding_depth:
            return set()
        context = function.instance_context
        if context and context.receiver == name:
            return {context.owner_symbol}
        result: set[str] = set()
        definitions = self.incoming.get(key, [])
        for source in definitions:
            node = function.nodes[source]
            for call in node.calls:
                if not (call.result_pattern and call.result_pattern.name == name):
                    continue
                fact = self.index.callsite(call.callsite_id)
                for target in fact.targets if fact else ():
                    if target.relation == "instantiates" and target.target_symbol in self.index.artifact.types:
                        result.add(target.target_symbol)
                    elif (callee := self.index.callable(target.target_symbol)) and callee.return_type:
                        result.update(self._type_symbols(callee.return_type, function.location.path))
            for transfer in node.value_transfers:
                if transfer.output_variable == name and transfer.reference_source:
                    result.update(self._receiver_types(function, source, transfer.reference_source, seen | {key}))
        if definitions == [function.entry_node_id] or not definitions and name in function.parameters:
            for fact in self.index.variable_types_for(function.symbol_id, name, role="parameter"):
                result.update(self._type_symbols(fact.type_literal, function.location.path))
        return result

    def _type_symbols(self, literal: str, path: str) -> set[str]:
        """全限定类型直接绑定，短名仅接受当前文件或全项目唯一声明。"""
        if literal in self.index.artifact.types:
            return {literal}
        values = self.type_names.get(literal, [])
        local = [value for value in values if value.split("::", 1)[0] == path]
        return set(local if local else values if len(values) == 1 else [])
