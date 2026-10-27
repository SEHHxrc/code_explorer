"""组合引用正常出口摘要；符号形参只作查询模板，求解仍使用唯一 ObjectReferenceIndex。"""

from collections import defaultdict
from dataclasses import dataclass, replace

from backend.app.services.program_graph import FunctionProgramGraph
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.value_binding.access_paths import AccessPath, canonical_path, member_path

from .object_contracts import LocalInstance, ObjectReferenceWrite
from .object_references import ObjectReferenceIndex
from .post_calls import normal_exit_values

FORMAL_OBJECT_PREFIX = "formal-object:"


@dataclass(frozen=True)
class ReferenceInterface:
    """一次公共引用查询的写回和借用返回；现有引用不会被当作工厂新对象克隆。"""

    writes: tuple[ObjectReferenceWrite, ...] = ()
    return_source: str = ""
    return_origins: tuple[FieldValueOrigin, ...] = ()


def reference_interface(function: FunctionProgramGraph, local_instances: list[LocalInstance], *, max_fields: int = 256
                        ) -> ReferenceInterface:
    """把形参/接收者作为符号对象查询正常出口；不将这些临时声明提升为项目对象事实。

    调用方必须再次验证实际对象、目的字段与来源；标量参数碰巧出现于复制表达式不证明对象身份。
    未知分支保留 unresolved；不会以不完整摘要清除旧对象，也不执行被分析函数。
    """
    context = function.instance_context
    roots = set(function.parameters) | ({context.receiver} if context is not None else set())
    paths: dict[str, set[str]] = defaultdict(set)
    written, origins = set(), defaultdict(list)
    for node in function.nodes.values():
        if node.node_id == function.entry_node_id or node.node_id.startswith("normal-values:"):
            continue
        for name in (*node.uses, *node.definitions):
            path = canonical_path(name, selectors=True)
            if path is None or path.root not in roots:
                continue
            for position, segment in enumerate(path.segments):
                parent = AccessPath(path.root, path.segments[:position]).render()
                paths[parent].add(segment.render() if segment.kind in {"index", "unknown"} else str(segment.value))
            if name in node.definitions and path.segments:
                written.add(name)
                if node.location is not None:
                    origins[name].append(FieldValueOrigin(symbol=function.symbol_id, location=node.location,
                        expression=node.code, input_names=list(node.uses), output_names=[name]))
    if sum(len(fields) for fields in paths.values()) + len(roots) > max_fields:
        return ReferenceInterface()
    nodes = dict(function.nodes)
    entry = nodes[function.entry_node_id]
    references = {member_path(parent, field): [FORMAL_OBJECT_PREFIX + member_path(parent, field)]
                  for parent, fields in paths.items() for field in fields}
    # 每个词法前缀只建立一次符号对象，RHS 形参或子路径可以在同一环境流经普通赋值。
    names = roots | set(paths) | {member_path(parent, field) for parent, fields in paths.items() for field in fields}
    seeds = [LocalInstance(entry.node_id, "", FORMAL_OBJECT_PREFIX + name, "formal", name, tuple(sorted(paths.get(name, ()))))
             for name in sorted(names, key=lambda value: (len(canonical_path(value, selectors=True).segments), value))]
    nodes[entry.node_id] = entry.model_copy(update={"object_reference_bindings": references})
    view, normal_id = normal_exit_values(function.model_copy(update={"nodes": nodes}), written)
    resolver = ObjectReferenceIndex(view, [*seeds, *local_instances])
    if not resolver.available or normal_id not in resolver.before:
        return ReferenceInterface()
    normal = view.nodes[normal_id]
    result = []
    for name in sorted(written):
        binding = resolver.at(normal, name)
        identity = binding.unique_allocation
        if identity == FORMAL_OBJECT_PREFIX + name:
            continue
        source = identity.removeprefix(FORMAL_OBJECT_PREFIX) if identity is not None and identity.startswith(FORMAL_OBJECT_PREFIX) else ""
        snapshot = resolver.snapshot(normal, name, max_objects=max_fields) if identity is not None and not source else None
        if snapshot is not None:
            external = tuple((item.allocation_id, item.allocation_id.removeprefix(FORMAL_OBJECT_PREFIX))
                             for item in snapshot.objects if item.allocation_id.startswith(FORMAL_OBJECT_PREFIX))
            external_ids = {identity for identity, _ in external}
            snapshot = replace(snapshot, objects=tuple(item for item in snapshot.objects if item.allocation_id not in external_ids),
                references=tuple(item for item in snapshot.references if item.allocation_id not in external_ids), external_paths=external)
        result.append(ObjectReferenceWrite(name, source, snapshot, tuple(origins[name]),
                                          unresolved=identity is None or not source and snapshot is None))
    # 仅所有正常出口都显式返回同一外部引用时复用实际对象；混合/隐式返回不猜测。
    exits = [view.nodes[edge.source] for edge in view.edges if edge.kind == "cfg" and edge.target == normal_id]
    returned, return_origins = set(), []
    for node in exits:
        names = node.return_values[0] if node.kind == "return" and len(node.return_values) == 1 and node.return_value_forms == ["reference"] else []
        identity = resolver.at(node, names[0], write=names[0] in node.definitions).unique_allocation if len(names) == 1 else None
        returned.add(identity.removeprefix(FORMAL_OBJECT_PREFIX) if identity and identity.startswith(FORMAL_OBJECT_PREFIX) else "")
        if node.location is not None:
            return_origins.append(FieldValueOrigin(symbol=function.symbol_id, location=node.location,
                expression=node.code, input_names=list(node.uses), output_names=list(names)))
    return ReferenceInterface(tuple(result), next(iter(returned)) if len(returned) == 1 and "" not in returned else "", tuple(return_origins))
