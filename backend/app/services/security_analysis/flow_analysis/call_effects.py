"""规则声明的简单输出参数写入；通过私有后置操作复用公共 CFG/DFG。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphNode
from backend.app.services.program_graph.passes import (
    ReachingDefinitionsPass,
    ValueFlowPass,
)

from ..contracts import StaticSecurityFact
from .post_calls import append_post_operations


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
        可处理包含纯接收者/实参嵌套求值的最外层输出调用；多个平级调用或嵌套输出仍不猜顺序。
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
                candidates = [call for call in node.calls if call.callsite_id in facts]
                if len(candidates) != 1:
                    continue
                call = candidates[0]
                start = (call.location.line, call.location.column)
                end = (call.location.end_line, call.location.end_column)
                if any(not (start <= (other.location.line, other.location.column)
                            and (other.location.end_line, other.location.end_column) <= end)
                       for other in node.calls):
                    continue
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
            operations: dict[str, list[ProgramGraphNode]] = {}
            for original, names in effects.items():
                identity, call = positions[original]
                operations[original] = [ProgramGraphNode(
                    node_id=identity,
                    kind="operation",
                    language=function.language,
                    method_id=function.method_id,
                    location=call.location,
                    code=function.nodes[original].code,
                    definitions=sorted(names),
                    certainty="may",
                    provenance="inferred",
                )]
            view = append_post_operations(function, operations).model_copy(
                update={
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
