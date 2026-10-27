"""跨返回/副作用边界的对象快照装配；只重映射已有身份，不求解引用或创建结构调用边。"""

from dataclasses import dataclass, replace
import hashlib

from backend.app.services.program_graph import ProgramGraphNode, ProgramValueTransfer
from backend.app.services.value_binding.objects import storage_slot, instance_field_name
from backend.app.services.value_binding.access_paths import member_path

from .object_contracts import LocalInstance, ObjectSnapshot


@dataclass(frozen=True)
class SnapshotField:
    """一个已知对象的直属字段；相对路径用于边界展示，分配身份隔离调用和共享对象。"""

    path: str
    field_name: str
    owner_symbol: str
    allocation_id: str


@dataclass(frozen=True)
class ImportedSnapshot:
    """一次私有导入操作及其局部实例/字段映射；输入快照和原程序图均不就地修改。"""

    node: ProgramGraphNode
    instances: tuple[LocalInstance, ...]
    fields: tuple[SnapshotField, ...]
    allocations: tuple[tuple[str, str], ...]


def snapshot_fields(snapshot: ObjectSnapshot) -> tuple[SnapshotField, ...]:
    """列出可达对象字段，按类型保留工厂变体；不复制源码、无关对象或递归展开环。"""
    return tuple(SnapshotField(item.path, field, variant.owner_symbol, item.allocation_id)
                 for item in snapshot.objects for variant in item.variants for field in variant.fields)


def import_snapshot(snapshot: ObjectSnapshot, node: ProgramGraphNode, root: str, *, context_id: str,
                    depth: int = 0, root_allocation_id: str | None = None,
                    external_allocations: tuple[tuple[str, str], ...] = ()) -> ImportedSnapshot:
    """把局部新分配按调用上下文重映射一次；共享子对象仍共享，虚构变量只用于私有引用环境。

    返回操作包含普通字段定义/类型槽和引用写入；字段值通过既有 IRValueBoundary 接入，
    不把对象地址或整个对象当作污点内容。只用于调用边界中新创建的对象，不复制外部共享存储。
    """
    identities = {item.allocation_id: "allocation:" + hashlib.sha256(
        (context_id + "\x1f" + item.allocation_id).encode()).hexdigest()[:24] for item in snapshot.objects}
    if root_allocation_id is not None:
        identities[snapshot.root_id] = root_allocation_id
    identities.update(external_allocations)
    paths = {item.allocation_id: root + item.path for item in snapshot.objects}
    instances = tuple(replace(variant, node_id=node.node_id, callsite_id=context_id,
        allocation_id=identities[item.allocation_id],
        result_name=root if item.allocation_id == snapshot.root_id else "object_ref_" + identities[item.allocation_id].split(":")[-1],
        factory_depth=depth) for item in snapshot.objects for variant in item.variants)
    fields = snapshot_fields(snapshot)
    definitions, slots = set(node.definitions), dict(node.definition_object_slots)
    transfers = list(node.value_transfers)
    references = dict(node.object_reference_bindings)
    for item in fields:
        parent = paths[item.allocation_id]
        for output, owner in ((member_path(parent, item.field_name), None),
                              (instance_field_name(parent, item.owner_symbol, item.field_name), item.owner_symbol)):
            definitions.add(output)
            slots[output] = storage_slot(identities[item.allocation_id], item.field_name, owner)
            if not any(transfer.output_variable == output for transfer in transfers):
                transfers.append(ProgramValueTransfer(output_variable=output, transfer_kind="assignment", provenance="inferred"))
    for link in snapshot.references:
        if link.target_id not in identities:
            continue  # 符号形参未绑定到实际对象时只传字段值，不伪造指向一个新克隆对象。
        output = member_path(paths[link.allocation_id], link.field_name)
        references[output] = sorted(set(references.get(output, ())) | {identities[link.target_id]})
    return ImportedSnapshot(node.model_copy(update={"definitions": sorted(definitions),
        "definition_object_slots": slots, "value_transfers": transfers, "object_reference_bindings": references}),
        instances, fields, tuple(identities.items()))
