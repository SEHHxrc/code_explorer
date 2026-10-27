"""把已有值传播路线编译为临时调用上下文 CFG，复用唯一的路径条件求解器。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from backend.app.services.value_binding.identities import value_identity
from .conditions import ConditionExpression, ConditionPathBinding, ConditionPathSegment, PathConditionProof
from .contracts import FunctionProgramGraph, ProgramGraphEdge, ProgramGraphNode
from .path_conditions import PathConditionQuery


@dataclass(frozen=True)
class ConditionRoutePart:
    """一个已选函数激活片段；入口绑定必须来自现有调用/返回/值边界，不自己解析调用。"""

    function: FunctionProgramGraph
    frame_id: str
    start_node_id: str
    end_node_id: str
    value_edges: tuple[ProgramGraphEdge, ...] = ()
    bindings: tuple[ConditionPathBinding, ...] = ()
    skip_start_effects: bool = False
    skip_end_effects: bool = False
    skip_node_effects: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    def finish(self, end_node_id: str, edges: list[ProgramGraphEdge], *, before_call: bool = False) -> ConditionRoutePart:
        """以真实局部值流结束当前片段；进入已选调用前不提前执行其返回赋值。"""
        return replace(self, end_node_id=end_node_id, value_edges=tuple(edges), skip_end_effects=before_call)


class ConditionRouteQuery:
    """只组合指定路线，不枚举全仓库调用图；调用者与被调用者的标量身份按激活帧隔离。"""

    def __init__(self, query: PathConditionQuery | None = None, *, max_nodes: int = 10000, max_parts: int = 32) -> None:
        """注入同一标量求解器；临时图达到构建预算则保留 unknown。"""
        if min(max_nodes, max_parts) <= 0:
            raise ValueError("条件路线构建预算必须为正数。")
        self.query = query or PathConditionQuery()
        self.max_nodes, self.max_parts = max_nodes, max_parts

    def prove(self, parts: tuple[ConditionRoutePart, ...]) -> PathConditionProof:
        """把已存在的调用/返回复制编成公共标量操作，返回原始位置和边 ID，不交付临时全图。"""
        summaries = [ConditionPathSegment(method_id=part.function.method_id, frame_id=part.frame_id,
            start_node_id=part.start_node_id, end_node_id=part.end_node_id, bindings=list(part.bindings)) for part in parts]
        base = PathConditionProof(scope="interprocedural", segments=summaries, solver="z3.scalar-route.v1")
        if not parts or len(parts) > self.max_parts or sum(len(part.function.nodes) + len(part.bindings) for part in parts) > self.max_nodes:
            return base.model_copy(update={"status": "unknown", "truncated": True, "reasons": ["condition_route_budget_exceeded"]})
        nodes: dict[str, ProgramGraphNode] = {}
        edges: list[ProgramGraphEdge] = []
        data_edges: list[ProgramGraphEdge] = []
        originals: dict[str, tuple[str, str]] = {}
        edge_originals: dict[str, str] = {}
        source_nodes: dict[tuple[str, str], ProgramGraphNode] = {}
        points: list[str] = []
        gaps = set(reason for part in parts for reason in part.gaps)
        for part in parts:
            source_nodes.update({(part.frame_id, key): value for key, value in part.function.nodes.items()})

        def scoped(frame_id: str, node: ProgramGraphNode, name: str, *, write: bool) -> str:
            """激活帧隔离标量名称；读取/写入仍复用既有声明和值槽身份。"""
            identity = value_identity(node, name, write=write)
            return identity if identity.startswith("object:") else frame_id + "|" + identity

        previous_end = ""
        root_entry = ""
        for index, part in enumerate(parts):
            function = part.function
            if part.start_node_id not in function.nodes or part.end_node_id not in function.nodes:
                return base.model_copy(update={"status": "unknown", "reasons": ["condition_route_node_not_mapped"]})
            prefix = "part:" + str(index) + ":"
            bound_names = {binding.target_name for binding in part.bindings}
            for key, node in function.nodes.items():
                node_id = prefix + key
                originals[node_id] = (key, part.frame_id)
                skip = (key in part.skip_node_effects or (key == part.start_node_id and part.skip_start_effects) or
                        (key == part.end_node_id and part.skip_end_effects))
                if skip and len(node.calls) > 1:
                    gaps.add("multiple_calls_in_boundary_operation")
                definitions = [] if skip else [name for name in node.definitions
                    if not (key == part.start_node_id and name in bound_names)]
                nodes[node_id] = node.model_copy(update={"node_id": node_id, "definitions": definitions,
                    "calls": [] if skip else node.calls, "scalar_values": {} if skip else node.scalar_values,
                    "definition_value_ids": {name: scoped(part.frame_id, node, name, write=True) for name in node.definitions},
                    "use_value_ids": {name: scoped(part.frame_id, node, name, write=False) for name in node.uses},
                    "definition_object_slots": {}, "use_object_slots": {}})
            for edge in function.edges:
                if edge.kind != "cfg" or edge.source == part.end_node_id:
                    continue
                cloned = edge.model_copy(update={"edge_id": prefix + edge.edge_id, "source": prefix + edge.source, "target": prefix + edge.target,
                    "completion_push": part.frame_id + "|" + edge.completion_push if edge.completion_push else "",
                    "completion_pop": [part.frame_id + "|" + token for token in edge.completion_pop]})
                edges.append(cloned)
                edge_originals[cloned.edge_id] = edge.edge_id
            for edge in part.value_edges:
                data_edges.append(edge.model_copy(update={"source": prefix + edge.source, "target": prefix + edge.target,
                    "read_value_id": scoped(part.frame_id, function.nodes[edge.target], edge.read_variable or edge.variable or "", write=False)}))
            # 首段必须从真实函数入口到达 Source，后续从实际调用/返回落点继续，不能重跑调用者前缀。
            start = prefix + (function.entry_node_id if index == 0 else part.start_node_id)
            if not root_entry:
                root_entry = start
            bridge_end = previous_end
            for position, binding in enumerate(part.bindings):
                source = source_nodes.get((binding.source_frame, binding.source_node_id))
                if source is None:
                    gaps.add("condition_binding_source_not_mapped")
                    continue
                bridge_id = prefix + "binding:" + str(position)
                expression = binding.expression or binding.literal or ConditionExpression(kind="name", name=binding.source_name, reason="")
                source_names = expression.names()
                target_token = "binding_output"
                nodes[bridge_id] = ProgramGraphNode(node_id=bridge_id, kind="assignment", language=function.language,
                    method_id=function.method_id, location=function.nodes[part.start_node_id].location, provenance="inferred",
                    definitions=[target_token], uses=list(source_names), scalar_values={target_token: expression},
                    use_value_ids={name: scoped(binding.source_frame, source, name, write=not binding.reads_input) for name in source_names},
                    definition_value_ids={target_token: scoped(part.frame_id,
                        function.nodes.get(binding.target_node_id, function.nodes[part.start_node_id]), binding.target_name, write=True)})
                if bridge_end:
                    edges.append(ProgramGraphEdge(edge_id=bridge_id + ":in", kind="cfg", source=bridge_end, target=bridge_id))
                else:
                    gaps.add("initial_condition_binding_without_caller")
                bridge_end = bridge_id
            if bridge_end:
                edges.append(ProgramGraphEdge(edge_id=prefix + "enter", kind="cfg", source=bridge_end, target=start))
            points.extend([prefix + part.start_node_id, *(prefix + edge.target for edge in part.value_edges), prefix + part.end_node_id])
            previous_end = prefix + part.end_node_id
        compiled = parts[0].function.model_copy(update={"method_id": "condition-route", "nodes": nodes, "edges": edges,
            "entry_node_id": root_entry, "exit_node_id": previous_end, "control_dependence": None})
        result = self.query.prove(compiled, points, value_edges=data_edges)
        conditions = [condition.model_copy(update={"node_id": originals.get(condition.node_id, (condition.node_id, ""))[0],
            "frame_id": originals.get(condition.node_id, ("", ""))[1],
            "cfg_edge_id": edge_originals.get(condition.cfg_edge_id, condition.cfg_edge_id)}) for condition in result.conditions]
        return result.model_copy(update={"scope": "interprocedural", "solver": base.solver, "method_id": "", "segments": summaries,
            "status": "unknown" if gaps else result.status, "reasons": sorted(set(result.reasons) | gaps), "conditions": conditions,
            "waypoint_node_ids": [originals.get(key, (key, ""))[0] for key in result.waypoint_node_ids],
            "cfg_edge_ids": [edge_originals[key] for key in result.cfg_edge_ids if key in edge_originals],
            "unsat_condition_node_ids": [originals.get(key, (key, ""))[0] for key in result.unsat_condition_node_ids],
            "assumptions": [*result.assumptions, "只组合已选调用/返回和值边界；未选择的调用仍按未知副作用处理。"],
            "limitation": "指定调用上下文的有界标量组合；SAT 不证明可利用性，存在未建模边界时保持 unknown。"})
