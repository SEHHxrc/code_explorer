"""把公共集合分配事实接入现有对象引用/字段写回接口；不新增引用或污点求解算法。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_graph import ProgramGraphArtifact
from .object_contracts import LocalInstance
from .diagnostics import FlowDiagnostics


def collection_instances(graph: ProgramGraphArtifact, diagnostics: FlowDiagnostics | None = None) -> dict[str, list[LocalInstance]]:
    """按语法分配位置生成独立集合身份；原 ProgramGraph 不保存消费者私有堆状态。"""
    result: dict[str, list[LocalInstance]] = defaultdict(list)
    for function in graph.functions.values():
        for node in function.nodes.values():
            for root, allocation in node.collection_allocations.items():
                key = hashlib.sha256((node.node_id + "\x1f" + root).encode()).hexdigest()[:24]
                result[function.method_id].append(LocalInstance(node.node_id, "", "collection:" + key,
                    "collection:" + allocation.kind, root, allocation.members,
                    storage_kind=allocation.storage_kind, collection=allocation))
                if not allocation.complete and diagnostics is not None:
                    diagnostics.record("collection_layout_incomplete", symbol=function.symbol_id, node_id=node.node_id,
                        value=root, reason_detail="dynamic_spread_or_literal_budget")
    return result
