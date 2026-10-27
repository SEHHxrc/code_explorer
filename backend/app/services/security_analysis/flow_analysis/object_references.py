"""已识别局部实例的有界 CFG 引用环境；共享字段身份，不创建调用目标或新增污点求解器。"""

from collections import defaultdict, deque
from dataclasses import replace
import networkx as nx

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphNode
from backend.app.services.program_graph import ProgramValueTransfer
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.program_graph.passes import ReachingDefinitionsPass
from backend.app.services.value_binding.identities import value_identity, variable_root
from backend.app.services.value_binding.objects import CollectionSummarySlot, StorageValueSlot, storage_slot, instance_field_name
from backend.app.services.value_binding.access_paths import AccessPathSegment, canonical_path, descendant, member_path

from .diagnostics import FlowDiagnostics
from .instance_common import escaped_receivers, is_reference_assignment, rebuild, reference_alias_groups
from .object_contracts import LocalInstance, ObjectBinding, ObjectSnapshot, SnapshotObject, SnapshotReference


def _merge(bindings: list[ObjectBinding]) -> ObjectBinding:
    """合并 CFG 前驱的 may 引用集合；未知分支不能在合并时丢失。"""
    return ObjectBinding(frozenset().union(*(item.allocations for item in bindings)),
                         frozenset().union(*(item.alias_nodes for item in bindings)))


class ObjectReferenceIndex:
    """局部名称与字段引用的流敏感环境；嵌套路径逐跳解析，不将路径拼接当作堆身份。"""

    def __init__(self, function: FunctionProgramGraph, instances: list[LocalInstance],
                 diagnostics: FlowDiagnostics | None = None, *, max_updates: int = 100000,
                 max_depth: int = 16) -> None:
        """输入公共 CFG 与已确认实例，按工作列表计算，不依赖语言 AST 或运行时地址。"""
        self.function = function
        self.diagnostics = diagnostics
        self.max_depth = max_depth
        self.instances: dict[str, list[LocalInstance]] = defaultdict(list)
        self.storage_roots = {variable_root(item.result_name) for item in instances if item.storage_kind in {"value", "scalar"}}
        if any(node.object_reference_bindings or node.storage_invalidations for node in function.nodes.values()):
            self.storage_roots.update(variable_root(item.result_name) for item in instances)
        seeds: dict[str, list[LocalInstance]] = defaultdict(list)
        for item in instances:
            self.instances[item.allocation_id].append(item)
            seeds[item.node_id].append(item)
        predecessors: dict[str, set[str]] = defaultdict(set)
        successors: dict[str, set[str]] = defaultdict(set)
        input_edges = defaultdict(list)
        for edge in function.edges:
            if edge.kind == "cfg":
                predecessors[edge.target].add(edge.source)
                successors[edge.source].add(edge.target)
                input_edges[edge.target].append(edge)
        reachable = ReachingDefinitionsPass._reachable(function.entry_node_id, successors)
        cyclic: set[str] = set()
        cfg = nx.DiGraph()
        cfg.add_edges_from((edge.source, edge.target) for edge in function.edges if edge.kind == "cfg")
        for component in nx.strongly_connected_components(cfg):
            if len(component) > 1 or any(node in successors[node] for node in component):
                cyclic.update(component)
        self.cyclic_nodes = frozenset(cyclic)
        if diagnostics is not None:
            for node_id in seeds.keys() & cyclic:
                diagnostics.record("instance_alias_not_resolved", symbol=function.symbol_id, node_id=node_id,
                                   reason_detail="allocation_site_may_repeat_in_cycle")
        self.before: dict[str, dict[str, ObjectBinding]] = {node: {} for node in reachable}
        self.after: dict[str, dict[str, ObjectBinding]] = {node: {} for node in reachable}
        self.write_slots: dict[str, dict[str, StorageValueSlot]] = {}
        self.copy_projections: dict[str, list[ProgramValueTransfer]] = {}
        pending = deque([function.entry_node_id])
        queued = {function.entry_node_id}
        initialized: set[str] = set()
        self.available = True
        updates = 0
        state_entries = 0
        while pending:
            node_id = pending.popleft()
            queued.discard(node_id)
            node = function.nodes[node_id]
            # 未求值前驱是 bottom，不是实际执行路径上的“未定义”。否则循环回边会
            # 将循环外已确定的引用永久污染为 unknown。
            parents = predecessors[node_id] & initialized
            states = [(self.before[edge.source] if edge.source_before_write else self.after[edge.source])
                      for edge in input_edges[node_id] if edge.source in parents]
            keys = set().union(*states) if states else set()
            incoming = {key: _merge([state.get(key, ObjectBinding()) for state in states]) for key in keys}
            outgoing = dict(incoming)
            # 名称先建立新分配；构造器生成字段属于新对象，不能清掉重绑定前的旧对象。
            for name in node.definitions:
                if name.isidentifier():
                    key = value_identity(node, name, write=True)
                    old = incoming.get(key, ObjectBinding())
                    owned = [item for allocation in old.allocations for item in self.instances.get(allocation, ())
                             if item.result_name == name and item.storage_kind == "value"]
                    outgoing[key] = old if owned else ObjectBinding()
            allocated_roots = {item.result_name for item in seeds[node_id]}
            seed_keys = {}
            for item in seeds[node_id]:
                slot = self._slot(node, item.result_name, outgoing, write=True)
                key = (self._cell_key(node, item.result_name, write=True) if item.storage_kind == "scalar" else
                       value_identity(node, item.result_name, write=True) if item.result_name.isidentifier()
                       or item.result_name.startswith("call-result:") else slot.value_id if slot else None)
                if key is not None:
                    seed_keys[key] = ObjectBinding(frozenset({item.allocation_id, ""})
                                                  if node_id in cyclic else frozenset({item.allocation_id}))
                    outgoing[key] = seed_keys[key]
            outgoing.update(seed_keys)
            def addresses(name: str) -> dict[str, ObjectBinding]:
                """普通字段地址取操作前引用，分配生成字段取刚创建的新对象。"""
                return outgoing if any(descendant(name, root) for root in allocated_roots) else incoming
            # 普通字段地址使用操作前对象；同操作重绑定根并写其字段的复杂目标由逃逸检查拒绝。
            self.write_slots[node_id] = {name: slot for name in node.definitions
                if (slot := self._slot(node, name, addresses(name), write=True)) is not None}
            for name in node.definitions:
                if name not in allocated_roots and (slot := self.write_slots[node_id].get(name)) is not None \
                        and not any(item.storage_kind == "scalar" for item in self.instances.get(slot.allocation_id, ())):
                    outgoing[slot.value_id] = ObjectBinding()
                    if isinstance(slot, CollectionSummarySlot):
                        # 未知键可能改掉任一子对象引用，不能继续把旧 child 当成唯一实际对象。
                        for variant in self.instances.get(slot.allocation_id, ()):
                            for member in variant.fields:
                                identity = storage_slot(slot.allocation_id, member).value_id
                                old = incoming.get(identity, ObjectBinding())
                                outgoing[identity] = ObjectBinding(old.allocations | {""}, old.alias_nodes | {node_id})
            # 并行赋值的 RHS 全部读 incoming；字段 LHS 使用操作前对象，分配生成字段除外。
            for transfer in node.value_transfers:
                if is_reference_assignment(function, transfer):
                    source_name = transfer.input_variables[0]
                    source = incoming.get(self._cell_key(node, source_name), self._lookup(node, source_name, incoming)) \
                        if transfer.storage_mode == "address" else self._lookup(node, source_name, incoming)
                    output = transfer.output_variable
                    slot = self._slot(node, output, addresses(output), write=True)
                    key = value_identity(node, output, write=True) if output.isidentifier() else slot.value_id if slot else None
                    if key is not None:
                        value = ObjectBinding(source.allocations, source.alias_nodes | {node_id})
                        outgoing[key] = _merge([incoming.get(key, ObjectBinding()), value]) if transfer.certainty == "may" else value
            projections = []
            for transfer in node.value_transfers:
                if transfer.storage_mode != "value_copy" or not transfer.reference_source:
                    continue
                for output, source in self._copy_pairs(node, transfer.output_variable, transfer.reference_source, incoming, outgoing):
                    target_slot = self._slot(node, output, outgoing, write=True)
                    if target_slot is not None:
                        self.write_slots[node_id][output] = target_slot
                        outgoing[target_slot.value_id] = self._lookup(node, source, incoming)
                        projections.append(ProgramValueTransfer(output_variable=output, input_variables=[source],
                            transfer_kind="assignment", provenance="inferred", certainty=transfer.certainty))
            self.copy_projections[node_id] = projections
            # 分配值本身不应被构造调用的普通 scalar 传递覆盖。
            outgoing.update(seed_keys)
            for name, allocations in node.object_reference_bindings.items():
                slot = node.definition_object_slots.get(name) or self._slot(node, name, outgoing, write=True)
                key = value_identity(node, name, write=True) if name.isidentifier() or name.startswith("call-result:") and "." not in name \
                    else slot.value_id if slot is not None else None
                if key is not None:
                    outgoing[key] = ObjectBinding(frozenset(allocations or [""]), frozenset({node_id}))
                    if slot is not None:
                        self.write_slots[node_id][name] = slot
            # 导入快照的内层字段在该操作后才有完整引用路径；仍保存为原直属字段槽。
            if node.object_reference_bindings:
                self.write_slots[node_id].update((name, slot) for name in node.definitions
                    if (slot := self._slot(node, name, outgoing, write=True)) is not None)
            if node.certainty == "may":
                outgoing = {key: _merge([incoming.get(key, ObjectBinding()), value]) for key, value in outgoing.items()}
            if node.storage_invalidations:
                invalid = set(node.storage_invalidations)
                outgoing = {key: ObjectBinding(alias_nodes=value.alias_nodes) if "*" in invalid or value.allocations & invalid else value
                            for key, value in outgoing.items()}
            if node_id in initialized and incoming == self.before[node_id] and outgoing == self.after[node_id]:
                continue
            initialized.add(node_id)
            state_entries += len(incoming) + len(outgoing) - len(self.before[node_id]) - len(self.after[node_id])
            self.before[node_id], self.after[node_id] = incoming, outgoing
            updates += 1
            if updates > max_updates or state_entries > 100000 or any(len(item.alias_nodes) > 64 for item in outgoing.values()):
                self.available = False
                self.before.clear()
                self.after.clear()
                self.write_slots.clear()
                self.copy_projections.clear()
                if diagnostics is not None:
                    diagnostics.record("instance_alias_budget_exceeded", symbol=function.symbol_id, limit=max_updates,
                                       state_entries=state_entries, state_entry_limit=100000, alias_origin_limit=64)
                break
            for child in sorted(successors[node_id] & reachable):
                if child not in queued:
                    pending.append(child)
                    queued.add(child)
        if self.available and any(item.collection is not None for item in instances):
            for operation in function.nodes.values():
                for name in operation.uses:
                    # 登记后续实际出现的固定键，使未知写、快照和调用计划共享同一有界布局。
                    self._slot(operation, name, self.before.get(operation.node_id, {}))

    def at(self, node: ProgramGraphNode, name: str, *, write: bool = False) -> ObjectBinding:
        """查询某操作前/后的引用；缺失、不可达或超限均返回明确未知，而不是空的安全集合。"""
        table = self.after if write else self.before
        return self._lookup(node, name, table.get(node.node_id, {}), write=write)

    @staticmethod
    def _cell_key(node: ProgramGraphNode, name: str, *, write: bool = False) -> str:
        """标量存储单元与其中保存的指针值分开；不能把 p=x 与 p=&x 混为一谈。"""
        declarations = node.definition_declarations if write else node.use_declarations
        return "cell:" + declarations.get(name, "lexical:" + name)

    def _lookup(self, node: ProgramGraphNode, name: str, state: dict[str, ObjectBinding],
                *, write: bool = False) -> ObjectBinding:
        """在一个 CFG 状态读取名称或字段指向；标量、未知字段与深度超限均为未知引用。"""
        path = canonical_path(name, selectors=True)
        if path is not None and not path.segments:
            return state.get(value_identity(node, name, write=write), ObjectBinding())
        if name.endswith(".*"):
            parent = self._lookup(node, name[:-2], state, write=write)
            if parent.unique_allocation is not None and all(item.storage_kind != "scalar"
                    for item in self.instances.get(parent.unique_allocation, ())):
                return parent
        slot = self._slot(node, name, state, write=write)
        if slot is None:
            return ObjectBinding()
        result = state.get(slot.value_id, ObjectBinding())
        root = state.get(value_identity(node, variable_root(name), write=False), ObjectBinding())
        return ObjectBinding(result.allocations, result.alias_nodes | root.alias_nodes)

    def _slot(self, node: ProgramGraphNode, name: str, state: dict[str, ObjectBinding],
              *, write: bool = False) -> StorageValueSlot | None:
        """沿每个已知字段引用寻找最后一个直属槽；对象环只消耗路径段，不递归展开堆。"""
        path = canonical_path(name, selectors=True)
        if path is None:
            return None
        segments = list(path.segments)
        if len(segments) > self.max_depth:
            return None
        binding = state.get(value_identity(node, path.root, write=write), ObjectBinding())
        if not segments:
            cell = state.get(self._cell_key(node, path.root, write=write), ObjectBinding())
            instances = self.instances.get(cell.unique_allocation, ())
            return storage_slot(cell.unique_allocation, "__value__") if cell.unique_allocation is not None and any(
                item.storage_kind == "scalar" and item.result_name == path.root for item in instances) else None
        if segments[0].value == "__value__":
            binding = state.get(self._cell_key(node, path.root, write=write), binding)
        for position, segment in enumerate(segments):
            allocation = binding.unique_allocation
            if allocation is None:
                return None
            instances = self.instances.get(allocation, ())
            scalar = any(item.storage_kind == "scalar" for item in instances)
            if segment.kind == "attribute" and segment.value == "*" and not scalar:
                continue
            collections = [item.collection for item in instances if item.collection is not None]
            if segment.kind == "unknown":
                if position != len(segments) - 1 or not (collections or any("[?]" in item.fields for item in instances)):
                    return None
                if any(len(item.fields) >= 256 and "[?]" not in item.fields for item in instances):
                    return None
                self.instances[allocation] = [replace(item, fields=(*item.fields, "[?]"))
                    if "[?]" not in item.fields else item for item in instances]
                return CollectionSummarySlot(allocation_id=allocation, summary=True)
            if collections:
                if len(collections) != len(instances):
                    return None
                javascript = all(item.key_semantics == "javascript" for item in collections)
                if segment.kind != "index" and not javascript:
                    return None
                key = str(segment.value) if javascript else segment.value
                if self.function.language == "python" and type(key) is int and key < 0 and any(not item.complete for item in collections):
                    return CollectionSummarySlot(allocation_id=allocation, summary=True)
                if self.function.language == "python" and type(key) is int and key < 0 and all(
                        item.kind == "sequence" and item.complete for item in collections):
                    sizes = {len([field for field in item.fields if field != "[?]"]) for item in instances}
                    if len(sizes) == 1:
                        key += next(iter(sizes))
                field = AccessPathSegment("index", key).render()
                matches = [item for item in instances if item.collection.kind != "sequence" or field in item.fields]
                if matches and any(field not in item.fields for item in matches):
                    if any(len(item.fields) >= 256 for item in matches):
                        return None
                    # 字典新增固定键沿相同分配记录布局，供返回快照复用；不是按项目全局键展开。
                    self.instances[allocation] = [replace(item, fields=(*item.fields, field))
                        if item in matches and field not in item.fields else item for item in instances]
            else:
                field = segment.render() if segment.kind == "index" else "__value__" if segment.value == "*" else str(segment.value)
                matches = [item for item in instances if field in item.fields]
            if not matches:
                return None
            slot = storage_slot(allocation, field)
            if not collections and segment.kind == "attribute" and position == len(segments) - 2:
                marker = segments[-1].render()
                owner = next((item.owner_symbol for item in matches
                              if instance_field_name("", item.owner_symbol, field) == "." + field + marker), None)
                if owner is not None:
                    return storage_slot(allocation, field, owner)
            if position == len(segments) - 1:
                return slot
            if len(matches) != len(instances):
                return None
            binding = state.get(slot.value_id, ObjectBinding())
        return None

    def _copy_pairs(self, node: ProgramGraphNode, output: str, source: str,
                    before: dict[str, ObjectBinding], after: dict[str, ObjectBinding]) -> list[tuple[str, str]]:
        """值复制只展开内嵌值字段，指针字段复制引用而不深拷贝其目标；布局歧义不猜测。"""
        pending, result = [(output, source, 0)], []
        while pending:
            target, value, depth = pending.pop()
            left = self._lookup(node, target, after, write=True).unique_allocation
            right = self._lookup(node, value, before).unique_allocation
            targets, sources = self.instances.get(left, ()), self.instances.get(right, ())
            if len(targets) != 1 or len(sources) != 1 or targets[0].storage_kind != "value" \
                    or targets[0].owner_symbol != sources[0].owner_symbol or depth >= self.max_depth:
                continue
            for field in targets[0].fields:
                if field in targets[0].embedded_fields:
                    pending.append((member_path(target, field), member_path(value, field), depth + 1))
                else:
                    result.append((member_path(target, field), member_path(value, field)))
            if len(result) + len(pending) > 256:
                return []
        return result

    def possible(self, node: ProgramGraphNode, name: str, *, address: bool = False) -> list[LocalInstance]:
        """仅唯一分配身份可共享字段；不同具体类型的工厂变体可同属一个静态调用位置。"""
        binding = self.before.get(node.node_id, {}).get(self._cell_key(node, name), self.at(node, name)) if address else self.at(node, name)
        return self.instances.get(binding.unique_allocation, []) if binding.unique_allocation is not None else []

    def snapshot(self, node: ProgramGraphNode, name: str, *, allocation_id: str | None = None,
                 write: bool = False, max_objects: int = 256) -> ObjectSnapshot | None:
        """按分配身份遍历操作时刻的已知引用；共享/环只保存一次，未知标量不伪造子对象。"""
        state = (self.after if write else self.before).get(node.node_id, {})
        binding = self._lookup(node, name, state, write=write)
        root = allocation_id or binding.unique_allocation
        if not self.available or root is None or root not in binding.allocations or "" in binding.allocations:
            return None
        pending, captured, links = deque([(root, "")]), {}, []
        while pending:
            current, path = pending.popleft()
            if current in captured:
                continue
            variants = self.instances.get(current, ())
            if not variants or len(captured) >= max_objects or len(canonical_path("root" + path, selectors=True).segments) > self.max_depth:
                if self.diagnostics is not None:
                    self.diagnostics.record("object_snapshot_not_resolved", symbol=self.function.symbol_id,
                        node_id=node.node_id, reason_detail="missing_layout_or_snapshot_budget", limit=max_objects)
                return None
            captured[current] = SnapshotObject(current, path, tuple(variants))
            for field in sorted({field for variant in variants for field in variant.fields}):
                slot = storage_slot(current, field)
                value = state.get(slot.value_id, ObjectBinding())
                known = value.allocations - {""}
                if not known:
                    continue
                if value.unique_allocation is None or any(field not in variant.fields for variant in variants):
                    if self.diagnostics is not None:
                        self.diagnostics.record("object_snapshot_not_resolved", symbol=self.function.symbol_id,
                            node_id=node.node_id, reason_detail="ambiguous_nested_reference", value=member_path(name, field))
                    return None
                links.append(SnapshotReference(current, field, value.unique_allocation))
                pending.append((value.unique_allocation, member_path(path, field)))
            if len(links) > max_objects:
                if self.diagnostics is not None:
                    self.diagnostics.record("object_snapshot_not_resolved", symbol=self.function.symbol_id,
                        node_id=node.node_id, reason_detail="snapshot_reference_budget", limit=max_objects)
                return None
        return ObjectSnapshot(root, tuple(captured.values()), tuple(links))

    def alias_origins(self, node: ProgramGraphNode, name: str) -> list[FieldValueOrigin]:
        """保留真实别名赋值源码；分支合并为 may 证据，不宣称唯一执行轨迹。"""
        result = []
        origins = set(self.at(node, name).alias_nodes)
        allocations = set(self.at(node, name).allocations) - {""}
        path = canonical_path(name)
        if path is not None:
            for length in range(len(path.segments)):
                prefix = type(path)(path.root, path.segments[:length]).render()
                origins.update(self.at(node, prefix).alias_nodes)
                allocations.update(self.at(node, prefix).allocations - {""})
        for key in sorted(origins,
                          key=lambda key: (self.function.nodes[key].location.line if self.function.nodes[key].location else 0, key)):
            origin = self.function.nodes[key]
            if origin.location is not None and not (origin.provenance == "inferred" and origin.object_reference_bindings):
                result.append(FieldValueOrigin(symbol=self.function.symbol_id, location=origin.location,
                    expression=origin.code, input_names=list(origin.uses), output_names=list(origin.definitions)))
            for output, details in origin.object_alias_origins.items():
                if self.at(origin, output, write=True).allocations & allocations:
                    result.extend(details)
        return list({(item.symbol, item.location.location_id, item.expression, item.callsite_id): item for item in result}.values())

    def field_binding(self, node: ProgramGraphNode, name: str, *, write: bool) -> StorageValueSlot | None:
        """返回最终对象的直属字段槽；中间字段逐跳解析，绝不创建 child.value 复合字段。"""
        table = self.after if write else self.before
        if write and name in node.definitions:
            return self.write_slots.get(node.node_id, {}).get(name)
        return self._slot(node, name, table.get(node.node_id, {}), write=write)

    def bind_fields(self, function: FunctionProgramGraph) -> FunctionProgramGraph:
        """给字段 Def/Use 加精确值身份，保留原始变量名与源码，复用唯一到达定义/值流算法。"""
        nodes = {}
        for node in function.nodes.values():
            # 未知键是弱写：向该分配实际观察过的固定槽投影，不删除旧值；摘要保留其余键。
            extras, transfers = {}, list(node.value_transfers)
            for name in node.definitions:
                slot = self.field_binding(node, name, write=True)
                if not isinstance(slot, CollectionSummarySlot):
                    continue
                parent = name.rsplit("[?]", 1)[0]
                for instance in self.instances.get(slot.allocation_id, ()):
                    for member in instance.fields:
                        if member == "[?]":
                            continue
                        output = member_path(parent, member)
                        extras[output] = storage_slot(slot.allocation_id, member)
                        transfers.extend(transfer.model_copy(update={"output_variable": output, "certainty": "may", "weak_update": True})
                                         for transfer in node.value_transfers if transfer.output_variable == name)
            if extras:
                self.write_slots.setdefault(node.node_id, {}).update(extras)
                node = node.model_copy(update={"definitions": sorted(set(node.definitions) | set(extras)),
                    "value_transfers": transfers})
            # 规则声明的整体输出写入投影到实际值字段，避免 parent 的旧 Source 绕过子字段常量覆盖。
            if not node.calls and not node.value_transfers:
                definitions = set(node.definitions)
                for name in node.definitions:
                    if name in node.object_reference_bindings:
                        continue  # 改变指向值结构体的引用，不等于覆盖该目标的全部成员。
                    fields = self._aggregate_fields(node, name)
                    if fields:
                        definitions.discard(name)
                        definitions.update(fields)
                        for field in fields:
                            slot = self._slot(node, field, self.after.get(node.node_id, {}), write=True)
                            if slot is not None:
                                self.write_slots.setdefault(node.node_id, {})[field] = slot
                node = node.model_copy(update={"definitions": sorted(definitions)})
            projections = self.copy_projections.get(node.node_id, ())
            if projections:
                node = node.model_copy(update={"definitions": sorted(set(node.definitions) | {item.output_variable for item in projections}),
                    "uses": sorted(set(node.uses) | {name for item in projections for name in item.input_variables}),
                    "value_transfers": [*node.value_transfers, *projections]})
            writes = {name: slot for name in node.definitions
                      if (slot := self.field_binding(node, name, write=True)) is not None}
            reads = {name: slot for name in node.uses
                     if (slot := self.field_binding(node, name, write=False)) is not None}
            if self.diagnostics is not None:
                for name in (*node.uses, *node.definitions):
                    path = canonical_path(name, selectors=True)
                    if path is not None and len(path.segments) > self.max_depth:
                        self.diagnostics.record("instance_nested_path_budget_exceeded", symbol=function.symbol_id,
                                                node_id=node.node_id, limit=self.max_depth)
                    elif (path is not None and len(path.segments) > 1 and name not in writes and name not in reads
                          and self.at(node, path.root).unique_allocation is not None):
                        self.diagnostics.record("instance_nested_reference_not_resolved", symbol=function.symbol_id,
                            node_id=node.node_id, value=name, reason_detail="unknown_or_ambiguous_intermediate_object")
                for root in {variable_root(name) for name in (*node.uses, *node.definitions) if "." in name or "[" in name} - {""}:
                    binding = self.at(node, root)
                    if (binding.alias_nodes or binding.allocations - {""}) and binding.unique_allocation is None:
                        self.diagnostics.record("instance_alias_not_resolved", symbol=function.symbol_id, node_id=node.node_id,
                            receiver=root, reason_detail="ambiguous_or_unknown_allocation")
            nodes[node.node_id] = node.model_copy(update={
                "definition_object_slots": {**node.definition_object_slots, **writes},
                "use_object_slots": {**node.use_object_slots, **reads},
                # 已知存储在未知副作用后失去精确引用，不能退回词法同名匹配而串回旧字段。
                "definition_value_ids": {**node.definition_value_ids, **{name: "unresolved-storage:" + node.node_id + ":" + name
                    for name in node.definitions if ("." in name or "[" in name) and name not in writes
                    and (variable_root(name) in self.storage_roots or self.at(node, variable_root(name)).alias_nodes)}},
                "use_value_ids": {**node.use_value_ids, **{name: "unresolved-storage:" + node.node_id + ":" + name
                    for name in node.uses if ("." in name or "[" in name) and name not in reads
                    and (variable_root(name) in self.storage_roots or self.at(node, variable_root(name)).alias_nodes)}},
                "object_alias_origins": {name: origins for name in writes.keys() | reads.keys()
                    if (origins := self.alias_origins(node, name))},
            })
        return rebuild(function, nodes, limitations=[*function.limitations,
            f"嵌套字段逐跳绑定唯一已知对象，最多 {self.max_depth} 层；分支歧义、逃逸与未知副作用不证明安全，亦不证明运行时对象身份。"])

    def _aggregate_fields(self, node: ProgramGraphNode, root: str) -> list[str]:
        """把已知值存储的整体定义投影到成员；只展开内嵌值，不沿指针深拷贝或猜测未知布局。"""
        pending, result = [(root, 0)], []
        while pending:
            name, depth = pending.pop()
            binding = self.at(node, name, write=True)
            instances = self.instances.get(binding.unique_allocation, ())
            if len(instances) != 1 or instances[0].storage_kind != "value" or depth >= self.max_depth:
                continue
            instance = instances[0]
            for field in instance.fields:
                if field in instance.embedded_fields:
                    pending.append((member_path(name, field), depth + 1))
                else:
                    result.append(member_path(name, field))
            if len(pending) + len(result) > 256:
                return []
        return result


def constructor_reference_fields(function: FunctionProgramGraph, fields: set[str], normal_id: str) -> dict[str, str]:
    """用同一引用求解器提取正常出口的形参→直属字段关系；未知调用/形参对象修改不作纯存储摘要。

    临时形参身份仅用于摘要，不进入项目堆身份或污点事实；变换表达式和不一致分支不会产生关系。
    """
    context = function.instance_context
    if context is None or not fields or function.object_assignment != "reference":
        return {}
    roots = set(function.parameters) | {context.receiver}
    aliases = reference_alias_groups(function)
    rejected = escaped_receivers(function, local_objects=frozenset({context.receiver}))
    for root in roots - {context.receiver}:
        names = aliases.get(root, {root})
        if any(any(name.startswith(alias + ".") for name in (*node.definitions, *node.uses) for alias in names)
               or any(variable_root(call.receiver) in names for call in node.calls)
               for node in function.nodes.values()):
            rejected.add(root)
    seeds = [LocalInstance(function.entry_node_id, "", "parameter:" + root, context.owner_symbol,
                           root, tuple(sorted(name[len(context.receiver) + 1:] for name in fields))
                           if root == context.receiver else ()) for root in roots]
    references = ObjectReferenceIndex(function, seeds)
    normal = function.nodes[normal_id]
    parameters = {"parameter:" + root: root for root in roots - rejected - {context.receiver}}
    return {name: parameters[allocation] for name in fields
            if (allocation := references.at(normal, name).unique_allocation) in parameters}
