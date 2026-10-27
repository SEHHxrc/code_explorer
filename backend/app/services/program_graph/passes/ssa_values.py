"""把已有原生 SSA 定义—使用事实投影到公共值边；不重建调用目标或求解堆别名。"""

from collections import defaultdict, deque
from bisect import bisect_left, bisect_right
import hashlib

from backend.app.services.semantic_index.contracts import SSAFunctionFact, SSAInstructionFact
from backend.app.services.semantic_index.ssa import validate_ssa_function
from backend.app.services.value_binding.identities import value_identity

from ..contracts import FunctionProgramGraph, ProgramGraphEdge, ProgramGraphNode
from .value_flow import ValueFlowPass


class SSAProjectionUnavailable(ValueError):
    """整函数不能完整投影时的稳定原因；不能把部分 SSA 边与旧推断边混成成功结果。"""


class SSAValueProjection:
    """使用编译器值身份及 φ 操作数生成公共 Def-Use，源码名称只负责精确位置映射。

    当前接入 Go 交换事实的标量、直接调用/返回和无内存表达式。内存 Store/Load、闭包、
    异步/协程和未映射操作使整个函数回退；不把地址的操作数依赖当作内容流。
    """

    _OPERATIONS = frozenset({"DebugRef", "Phi", "BinOp", "UnOp", "ChangeType", "Convert",
        "ChangeInterface", "MakeInterface", "Extract", "Call", "Return", "If", "Jump", "Panic"})

    def __init__(self, function: FunctionProgramGraph, native: SSAFunctionFact) -> None:
        """接收单函数公共操作与同一符号的原生事实；只构建本次投影私有的索引。"""
        self.function, self.native = function, native
        self.positions = {key: (block, offset) for block, fact in enumerate(native.blocks)
                          for offset, key in enumerate(fact.instructions)}
        self.producers = {fact.result: fact for fact in native.instructions.values() if fact.result}
        self.reads: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.writes: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.write_positions: dict[tuple[str, str], set[tuple[int, int]]] = defaultdict(set)
        self.read_positions: dict[tuple[str, str], set[tuple[int, int]]] = defaultdict(set)
        self.by_identity: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.inert_reads: set[tuple[str, str]] = set()

    def apply(self) -> FunctionProgramGraph:
        """返回只含原 CFG 与原生 Def-Use 派生值流的新图；任何未映射读取均拒绝整函数投影。"""
        source, target = self.native.location, self.function.location
        if not self.native.blocks or (source.path, source.line, source.column, source.end_line, source.end_column) != (
                target.path, target.line, target.column, target.end_line, target.end_column):
            raise SSAProjectionUnavailable("native_function_location_not_mapped")
        if len(self.native.instructions) > 20000 or len(self.function.nodes) > 5000:
            raise SSAProjectionUnavailable("native_projection_budget_exceeded")
        try:
            validate_ssa_function(self.native)
        except ValueError as exc:
            raise SSAProjectionUnavailable(str(exc)) from exc
        if any(value.kind in {"free_variable", "global"} for value in self.native.values.values()):
            raise SSAProjectionUnavailable("native_external_storage_not_modelled")
        for fact in self.native.instructions.values():
            if fact.opcode not in self._OPERATIONS or fact.opcode == "UnOp" and fact.operator in {"*", "<-"}:
                raise SSAProjectionUnavailable("native_operation_not_modelled:" + fact.opcode + fact.operator)
            if fact.invocation_kind in {"invoke", "dynamic"}:
                raise SSAProjectionUnavailable("native_call_dispatch_not_modelled:" + fact.invocation_kind)
        if any(node.object_reference_bindings or node.storage_invalidations
               or node.definition_object_slots or node.use_object_slots
               or node.provenance != "observed" and node.kind not in {"method_entry", "method_exit"}
               for node in self.function.nodes.values()):
            raise SSAProjectionUnavailable("private_memory_or_framework_overlay")
        self._map_slots()
        for key, values in self.writes.items():
            if values:
                node, name = key
                self.by_identity[value_identity(self.function.nodes[node], name, write=True)].append(key)
        edges = []
        for node in self.function.nodes.values():
            for name in node.uses:
                key = (node.node_id, name)
                if key in self.inert_reads:
                    continue  # 原生 direct 调用的函数/包身份不是运行时数据；动态函数值已在能力检查拒绝。
                values = self.reads.get(key, set())
                if not values:
                    raise SSAProjectionUnavailable("native_read_not_mapped:" + name)
                identity = value_identity(node, name, write=False)
                if identity.startswith("lexical:") and not name.startswith("call-result:"):
                    raise SSAProjectionUnavailable("native_variable_identity_not_mapped:" + name)
                found = self._definitions(values, identity, key)
                for source_id, source_name in sorted(found):
                    material = "\x1f".join((self.function.method_id, source_id, node.node_id, source_name, name))
                    edges.append(ProgramGraphEdge(edge_id="ssa-def-use:" + hashlib.sha256(material.encode()).hexdigest()[:24],
                        source=source_id, target=node.node_id, kind="reaching_def", variable=name,
                        source_variable=source_name, source_value_id=identity, read_value_id=identity,
                        certainty="may", provenance="inferred"))
        view = self.function.model_copy(update={"edges": [edge for edge in self.function.edges if edge.kind == "cfg"] + edges})
        return ValueFlowPass().apply(view)

    @staticmethod
    def _contains(node: ProgramGraphNode, fact: SSAInstructionFact) -> bool:
        """仅精确文件与完整源码范围内匹配；不按同名或最近行号选择操作。"""
        outer, inner = node.location, fact.location
        return bool(outer and inner and outer.path == inner.path and inner.line and inner.column
                    and (outer.line, outer.column) <= (inner.line, inner.column)
                    and (inner.end_line, inner.end_column) <= (outer.end_line, outer.end_column))

    def _map_slots(self) -> None:
        """用参数、DebugRef 与调用位置映射公共读写槽；同语句读写无法区分时明确回退。"""
        entry = self.function.nodes[self.function.entry_node_id]
        parameters = [*self.native.parameters, *([self.native.receiver_parameter] if self.native.receiver_parameter else [])]
        for value in parameters:
            name = self.native.values[value].name
            if name in entry.definitions:
                self.writes[(entry.node_id, name)].add(value)
                self.write_positions[(entry.node_id, name)].add((0, -1))
        operations = [node for node in self.function.nodes.values() if node.kind not in {"method_entry", "method_exit"}]
        calls_by_location: dict[tuple[str, int, int, int, int], list[tuple[str, SSAInstructionFact]]] = defaultdict(list)
        for key, fact in self.native.instructions.items():
            location = fact.call_location
            if fact.opcode == "Call" and location is not None:
                calls_by_location[(location.path, location.line, location.column, location.end_line, location.end_column)].append((key, fact))
        refs: dict[str, list[tuple[str, SSAInstructionFact]]] = defaultdict(list)
        debug = sorted(((key, fact) for key, fact in self.native.instructions.items()
                        if fact.opcode == "DebugRef" and fact.source_name and len(fact.operands) == 1 and fact.location),
                       key=lambda pair: pair[1].location.line)
        lines = [fact.location.line for _, fact in debug]
        owners: dict[str, tuple[tuple[int, int], list[ProgramGraphNode]]] = {}
        for node in operations:
            if node.location is None:
                continue
            span = (node.location.end_line - node.location.line, node.location.end_column - node.location.column)
            for key, fact in debug[bisect_left(lines, node.location.line):bisect_right(lines, node.location.end_line)]:
                if self._contains(node, fact):
                    previous = owners.get(key)
                    if previous is None or span < previous[0]:
                        owners[key] = span, [node]
                    elif span == previous[0]:
                        previous[1].append(node)
        for key, fact in debug:
            if key in owners:
                matches = owners[key][1]
                if len(matches) != 1:
                    raise SSAProjectionUnavailable("native_operation_location_ambiguous")
                refs[matches[0].node_id].append((key, fact))
        for node in operations:
            for name in set(node.definitions) | set(node.uses):
                matches = [(key, fact) for key, fact in refs[node.node_id] if fact.source_name == name]
                written = name in node.definitions
                if written and matches:
                    # DebugRef 的 LHS 位于源码最左处，指令位置仍保留编译器的实际读后写顺序。
                    left = min((fact.location.line, fact.location.column) for _, fact in matches)
                    definitions = [(key, fact) for key, fact in matches if (fact.location.line, fact.location.column) == left]
                    if len(definitions) != 1 or name in node.uses and len(matches) == 1:
                        raise SSAProjectionUnavailable("native_read_write_not_separable:" + name)
                    for key, fact in definitions:
                        self.writes[(node.node_id, name)].update(fact.operands)
                        self.write_positions[(node.node_id, name)].add(self.positions[key])
                    matches = [(key, fact) for key, fact in matches if (key, fact) not in definitions]
                if name in node.uses:
                    for key, fact in matches:
                        self.reads[(node.node_id, name)].update(fact.operands)
                        self.read_positions[(node.node_id, name)].add(self.positions[key])
            for call in node.calls:
                matches = calls_by_location.get((call.location.path, call.location.line, call.location.column,
                                                 call.location.end_line, call.location.end_column), ())
                if len(matches) != 1:
                    raise SSAProjectionUnavailable("native_callsite_not_mapped")
                key, fact = matches[0]
                if fact.receiver_value is None:
                    for name in (call.receiver, call.name, call.receiver + "." + call.name):
                        slot = (node.node_id, name)
                        if name in node.uses and not self.reads.get(slot):
                            self.inert_reads.add(slot)
                if call.result_variable in node.definitions and fact.result:
                    self.writes[(node.node_id, call.result_variable)].add(fact.result)
                    self.write_positions[(node.node_id, call.result_variable)].add(self.positions[key])
                for index, names in enumerate(call.positional_arguments):
                    if index >= len(fact.call_arguments):
                        raise SSAProjectionUnavailable("native_argument_not_mapped")
                    for name in names:
                        if name.startswith("call-result:"):
                            self.reads[(node.node_id, name)].add(fact.call_arguments[index])
                            self.read_positions[(node.node_id, name)].add(self.positions[key])
        for node in self.function.nodes.values():
            for name in node.definitions:
                if not self.writes.get((node.node_id, name)) and name != "_":
                    raise SSAProjectionUnavailable("native_definition_not_mapped:" + name)
                if name != "_" and value_identity(node, name, write=True).startswith("lexical:") and not name.startswith("call-result:"):
                    raise SSAProjectionUnavailable("native_variable_identity_not_mapped:" + name)

    def _can_reach(self, source: tuple[str, str], target: tuple[str, str]) -> bool:
        """比较原生指令顺序和 CFG；后出现定义不可倒流，回边只建立 may 路径。"""
        for block, offset in self.write_positions[source]:
            for end_block, end_offset in self.read_positions[target]:
                if block == end_block and offset < end_offset:
                    return True
                pending, seen = deque(self.native.blocks[block].successors), set()
                while pending:
                    current = pending.popleft()
                    if current == end_block:
                        return True
                    if current not in seen:
                        seen.add(current)
                        pending.extend(self.native.blocks[current].successors)
        return False

    def _definitions(self, values: set[str], identity: str, target: tuple[str, str]) -> set[tuple[str, str]]:
        """沿 SSA/φ 追到同一声明的实际定义；在最近已映射定义停止，不跨调用猜返回内容。"""
        pending, seen, result = deque(values), set(), set()
        while pending:
            value = pending.popleft()
            if value in seen:
                continue
            seen.add(value)
            matches = {source for source in self.by_identity.get(identity, ()) if value in self.writes[source]
                       and self._can_reach(source, target)}
            if matches:
                result.update(matches)
                continue
            fact = self.producers.get(value)
            if fact is not None and fact.opcode not in {"Call", "Extract"}:
                pending.extend(fact.operands)
        return result
