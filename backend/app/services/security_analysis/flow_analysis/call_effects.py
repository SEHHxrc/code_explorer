"""规则声明的简单输出参数写入；通过私有后置操作复用公共 CFG/DFG。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphNode
from backend.app.services.program_graph.passes import (
    ReachingDefinitionsPass,
    ValueFlowPass,
)

from ..contracts import StaticSecurityFact


class CallOutputEffects:
    """只处理明确输出位置的具名缓冲区，不解析指针别名、返回内容或通用堆副作用。"""

    @staticmethod
    def node_id(method_id: str, callsite_id: str) -> str:
        """返回与源文件调用点关联的稳定后置写入身份，不伪装成源码中的调用。"""
        return (
            "call-output:"
            + hashlib.sha256((method_id + "\x1f" + callsite_id).encode()).hexdigest()[
                :24
            ]
        )

    @classmethod
    def prepare(
        cls, graph: ProgramGraphArtifact, sources: list[StaticSecurityFact]
    ) -> ProgramGraphArtifact:
        """输入原图和 Source 角色，返回仅在可定位调用后插入 may 输出定义的私有视图。

        调用返回状态码与缓冲区定义使用不同节点；重算现有到达定义和值流，不修改原图。
        单操作含多个调用时不猜测求值顺序，保留局限而不建立错误传播。
        """
        facts = {
            str(fact.metadata.get("callsite_id")): fact
            for fact in sources
            if (fact.metadata.get("value_flow") or {}).get("output_targets")
        }
        if not facts:
            return graph
        updated = dict(graph.functions)
        for function in graph.functions.values():
            effects = defaultdict(set)
            positions = {}
            for node in function.nodes.values():
                if len(node.calls) != 1:
                    continue
                call = node.calls[0]
                fact = facts.get(call.callsite_id)
                if fact is None or fact.symbol != function.symbol_id:
                    continue
                targets = (fact.metadata.get("value_flow") or {}).get(
                    "output_targets"
                ) or {}
                names = {
                    name
                    for position, name in targets.items()
                    if int(position) < len(call.positional_arguments)
                    and call.positional_arguments[int(position)] == [name]
                }
                if names:
                    identity = cls.node_id(function.method_id, call.callsite_id)
                    effects[node.node_id].update(names)
                    positions[node.node_id] = (identity, call)
            if not effects:
                continue
            nodes = dict(function.nodes)
            edges = [edge for edge in function.edges if edge.kind == "cfg"]
            for original, names in effects.items():
                identity, call = positions[original]
                nodes[identity] = ProgramGraphNode(
                    node_id=identity,
                    kind="operation",
                    language=function.language,
                    method_id=function.method_id,
                    location=call.location,
                    code=nodes[original].code,
                    definitions=sorted(names),
                    certainty="may",
                    provenance="inferred",
                )
                outgoing = [edge for edge in edges if edge.source == original]
                edges = [edge for edge in edges if edge.source != original]
                edges.extend(
                    edge.model_copy(
                        update={"source": identity, "edge_id": edge.edge_id + ":output"}
                    )
                    for edge in outgoing
                )
                if outgoing:
                    # 沿用已有边协议和确定的顺序，只把写入本身标记为 may。
                    edges.append(
                        outgoing[0].model_copy(
                            update={
                                "source": original,
                                "target": identity,
                                "edge_id": identity + ":cfg",
                                "branch": "normal",
                                "certainty": "must",
                            }
                        )
                    )
            view = function.model_copy(
                update={
                    "nodes": nodes,
                    "edges": edges,
                    "limitations": list(
                        dict.fromkeys(
                            (
                                *function.limitations,
                                "输出参数仅建立具名缓冲区的 may 写入；未验证调用成功、字节范围、NUL 终止、指针别名或同操作多调用顺序。",
                            )
                        )
                    ),
                }
            )
            updated[function.method_id] = ValueFlowPass().apply(
                ReachingDefinitionsPass().apply(view)
            )
        return graph.model_copy(update={"functions": updated})
