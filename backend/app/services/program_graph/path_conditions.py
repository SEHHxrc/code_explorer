"""沿具体 CFG 路线版本化标量，使用 Z3 核验有序值流节点的共同路径条件。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from backend.app.services.value_binding.identities import value_identity
from backend.app.services.value_binding.access_paths import canonical_path
from .conditions import BranchConstraint, ConditionExpression, PathConditionProof, PredicateImplicationProof
from .contracts import FunctionProgramGraph, ProgramGraphEdge, ProgramGraphNode


@dataclass
class _State:
    """一条候选执行前缀；环境与条件仅在该路线内有效，分支不共享可变状态。"""

    node_id: str
    progress: int
    values: dict[str, Any]
    assertions: tuple[Any, ...] = ()
    conditions: tuple[BranchConstraint, ...] = ()
    edges: tuple[str, ...] = ()
    nodes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    completions: tuple[str, ...] = ()
    value_gaps: dict[str, tuple[str, ...]] = field(default_factory=dict)


class PathConditionQuery:
    """统一的函数内有界符号路径查询；不会修改 CFG、删除污点边或确认净化有效。"""

    def __init__(self, *, max_states: int = 256, max_visits: int = 2, timeout_ms: int = 250) -> None:
        """设置单次核验上限；耗尽任何预算必须返回 unknown，不能以无解代替超限。"""
        if min(max_states, max_visits, timeout_ms) <= 0:
            raise ValueError("路径核验预算必须为正数。")
        self.max_states, self.max_visits, self.timeout_ms = max_states, max_visits, timeout_ms

    def implies(self, function: FunctionProgramGraph, node_id: str, required: ConditionExpression) -> PredicateImplicationProof:
        """核验目标操作读取前是否必需满足 required；调用者负责提供上下文正确的安全谓词。"""
        reachable = self.prove(function, [node_id])
        if reachable.status != "satisfiable_under_model":
            return PredicateImplicationProof(status="unknown", required_condition=required, reachability=reachable)
        counterexample = self.prove(function, [node_id], target_condition=ConditionExpression(kind="not", operands=(required,), reason=""))
        return PredicateImplicationProof(status="implied_under_model" if counterexample.status == "infeasible_under_model" else
            "counterexample_under_model" if counterexample.status == "satisfiable_under_model" else "unknown",
            required_condition=required, reachability=reachable, counterexample=counterexample)

    def prove(self, function: FunctionProgramGraph, waypoints: list[str], *, value_edges: list[ProgramGraphEdge] | None = None,
              target_condition: ConditionExpression | None = None) -> PathConditionProof:
        """核验按顺序经过给定节点及 Def-Use 存活条件的路线；结果仅相对于声明的标量模型。"""
        points = [item for index, item in enumerate(waypoints) if not index or item != waypoints[index - 1]]
        compiler_bound = any(node.definition_declarations or node.use_declarations for node in function.nodes.values())
        base = PathConditionProof(method_id=function.method_id, waypoint_node_ids=points, target_condition=target_condition,
            identity_basis="partial_compiler" if compiler_bound and any(node.binding_gaps for node in function.nodes.values()) else
                "compiler" if compiler_bound else "lexical",
            assumptions=["仅建模原始布尔、整数和字符串值；不证明动态重载、引用相等或隐式类型转换。",
                         "顺序比较使用数学整数，不证明机器整数溢出或浮点行为。",
                         "相对于现有 CFG 与完成原因栈；异常类型匹配、无调用的隐式异常及并发不在模型内。",
                         "名称复用公共声明/值槽；无编译器绑定时只使用词法身份，不证明遮蔽或别名精度。"])
        if not points or any(item not in function.nodes for item in points):
            return base.model_copy(update={"status": "unknown", "reasons": ["invalid_or_empty_waypoints"]})
        try:
            import z3
        except ImportError:
            return base.model_copy(update={"status": "unknown", "reasons": ["z3_not_available"]})
        # 每次查询独立 Context，避免并行项目共享 Z3 全局 Context。
        context = z3.Context()
        value = z3.Datatype("Scalar", ctx=context)
        value.declare("integer", ("int_value", z3.IntSort(ctx=context)))
        value.declare("string", ("str_value", z3.StringSort(ctx=context)))
        value.declare("boolean", ("bool_value", z3.BoolSort(ctx=context)))
        scalar = value.create()
        serial = 0

        def fresh() -> Any:
            """每次未知写入获得新版本，不复用旧分支谓词中的变量。"""
            nonlocal serial
            serial += 1
            return z3.Const("v" + str(serial), scalar)

        active_gaps: dict[str, tuple[str, ...]] = {}

        def evaluate(expression: ConditionExpression, node: ProgramGraphNode, env: dict[str, Any], reasons: list[str], domains: list[Any]) -> Any:
            """将已支持的纯表达式转为 SMT；未知子表达式保留自由值并披露原因。"""
            kind = expression.kind
            if kind == "literal" and type(expression.value) in {bool, int, str}:
                literal = expression.value
                return (scalar.boolean(z3.BoolVal(literal, ctx=context)) if type(literal) is bool else
                        scalar.integer(z3.IntVal(literal, ctx=context)) if type(literal) is int else
                        scalar.string(z3.StringVal(literal, ctx=context)))
            if kind == "name" and (canonical_path(expression.name) is not None or expression.name in node.use_value_ids):
                if not expression.name.isidentifier() and expression.name not in node.use_object_slots and expression.name not in node.use_value_ids:
                    reasons.append("condition_storage_slot_not_bound:" + expression.name)
                if expression.name in node.binding_gaps or (compiler_bound and expression.name not in node.use_declarations
                        and expression.name not in node.use_value_ids):
                    reasons.append("condition_declaration_not_mapped:" + expression.name)
                identity = value_identity(node, expression.name, write=False)
                reasons.extend(active_gaps.get(identity, ()))
                if identity not in env:
                    env[identity] = fresh()
                return env[identity]
            if kind == "arithmetic" and expression.operator in {"add", "sub", "mul", "neg"}:
                operands = [evaluate(item, node, env, reasons, domains) for item in expression.operands]
                if expression.operator == "neg" and len(operands) == 1:
                    domains.append(scalar.is_integer(operands[0]))
                    return scalar.integer(-scalar.int_value(operands[0]))
                if len(operands) == 2:
                    left, right = operands
                    integers = z3.And(scalar.is_integer(left), scalar.is_integer(right))
                    strings = z3.And(scalar.is_string(left), scalar.is_string(right))
                    domains.append(z3.Or(integers, strings) if expression.operator == "add" else integers)
                    first, second = scalar.int_value(left), scalar.int_value(right)
                    result = first + second if expression.operator == "add" else first - second if expression.operator == "sub" else first * second
                    return z3.If(strings, scalar.string(z3.Concat(scalar.str_value(left), scalar.str_value(right))), scalar.integer(result)) if expression.operator == "add" else scalar.integer(result)
            if kind in {"and", "or"} and len(expression.operands) == 2:
                left = truth(evaluate(expression.operands[0], node, env, reasons, domains))
                right_domains: list[Any] = []
                right = truth(evaluate(expression.operands[1], node, env, reasons, right_domains))
                enabled = left if kind == "and" else z3.Not(left)
                domains.extend(z3.Implies(enabled, item) for item in right_domains)
                return scalar.boolean(z3.And(left, right) if kind == "and" else z3.Or(left, right))
            operands = [evaluate(item, node, env, reasons, domains) for item in expression.operands]
            if kind == "not" and len(operands) == 1:
                return scalar.boolean(z3.Not(truth(operands[0])))
            if kind == "compare" and len(operands) == 2:
                left, right = operands
                if expression.operator in {"eq", "ne"}:
                    return scalar.boolean(left == right if expression.operator == "eq" else left != right)
                if expression.operator in {"lt", "le", "gt", "ge"}:
                    first, second = scalar.int_value(left), scalar.int_value(right)
                    comparison = {"lt": first < second, "le": first <= second, "gt": first > second, "ge": first >= second}[expression.operator]
                    domains.extend((scalar.is_integer(left), scalar.is_integer(right)))
                    return scalar.boolean(comparison)
            reasons.append(expression.reason or "condition_expression_not_modelled")
            return fresh()

        def truth(item: Any) -> Any:
            """原始标量模型中的真值；对象自定义真值和语言类型合法性不在该模型内。"""
            return z3.If(scalar.is_boolean(item), scalar.bool_value(item),
                         z3.If(scalar.is_integer(item), scalar.int_value(item) != 0, scalar.str_value(item) != ""))

        outgoing: dict[str, list[ProgramGraphEdge]] = defaultdict(list)
        for edge in function.edges:
            if edge.kind == "cfg":
                outgoing[edge.source].append(edge)
        pending = deque([_State(function.entry_node_id, 0, {})])
        deadline = monotonic() + self.timeout_ms / 1000
        explored = 0
        gaps: set[str] = set()
        unsat_nodes: set[str] = set()
        unknown_witness: _State | None = None
        conflict_conditions: tuple[BranchConstraint, ...] = ()
        conflict_edges: tuple[str, ...] = ()
        truncated = False
        data_edges = value_edges or []
        while pending:
            if explored >= self.max_states or monotonic() >= deadline:
                truncated = True
                gaps.add("path_condition_budget_exceeded")
                break
            state = pending.popleft()
            active_gaps = state.value_gaps
            explored += 1
            node = function.nodes[state.node_id]
            progress = state.progress
            if progress < len(points) and node.node_id == points[progress]:
                progress += 1
            # 给定 Def-Use 必须在路线中保持定义存活；不允许绕过确定覆盖再拼接旧值。
            killed = False
            for data_edge in data_edges:
                if data_edge.source not in state.nodes or data_edge.target in state.nodes or node.node_id in {data_edge.source, data_edge.target}:
                    continue
                identity = data_edge.read_value_id or value_identity(function.nodes[data_edge.target], data_edge.read_variable or data_edge.variable or "", write=False)
                for name in node.definitions:
                    writes = [item for item in node.value_transfers if item.output_variable == name]
                    if value_identity(node, name, write=True) == identity and node.certainty == "must" and not any(item.weak_update for item in writes):
                        killed = True
            if progress == len(points):
                if target_condition is not None:
                    reasons = list(state.reasons)
                    domains: list[Any] = []
                    predicate = truth(evaluate(target_condition, node, dict(state.values), reasons, domains))
                    solver = z3.Solver(ctx=context)
                    solver.set(timeout=max(1, int((deadline - monotonic()) * 1000)))
                    solver.add(*state.assertions, *domains, predicate)
                    outcome = solver.check()
                    if outcome == z3.unsat:
                        gaps.update(reasons)
                        continue
                    if outcome == z3.unknown:
                        reasons.append("solver_unknown:" + solver.reason_unknown())
                    state.reasons = tuple(dict.fromkeys(reasons))
                if not state.reasons:
                    return base.model_copy(update={"status": "satisfiable_under_model", "cfg_edge_ids": list(state.edges),
                        "conditions": list(state.conditions), "trace_kind": "witness", "explored_states": explored})
                unknown_witness = unknown_witness or state
                gaps.update(state.reasons)
                continue
            env, reasons = dict(state.values), list(state.reasons)
            domains = []
            if node.node_id in state.nodes:
                reasons.append("repeated_node_requires_loop_invariant")
            updates, update_gaps = {}, {}
            for name, expression in node.scalar_values.items():
                if name not in node.definitions:
                    continue
                identity = value_identity(node, name, write=True)
                expression_gaps: list[str] = []
                updates[identity] = evaluate(expression, node, env, expression_gaps, domains)
                update_gaps[identity] = tuple(dict.fromkeys(expression_gaps))
            value_gaps = dict(state.value_gaps)
            # 赋值 RHS 一律读取旧环境，然后同时写回；未知写入及 weak update 不复用旧版本。
            for name in node.definitions:
                identity = value_identity(node, name, write=True)
                env[identity] = updates.get(identity, fresh())
                value_gaps[identity] = update_gaps.get(identity, ())
                if any(item.output_variable == name and item.weak_update for item in node.value_transfers):
                    value_gaps[identity] = ("conditional_write_not_modelled",)
                    env[identity] = fresh()
            if node.certainty == "may" and node.definitions:
                for name in node.definitions:
                    identity = value_identity(node, name, write=True)
                    env[identity] = fresh()
                    value_gaps[identity] = ("conditional_write_not_modelled",)
            # 调用可能影响捕获值/全局状态；不把调用前条件带过未知副作用。
            if node.calls:
                env = {key: fresh() for key in env}
                value_gaps = {key: ("call_side_effects_not_modelled",) for key in env}
            for edge in outgoing.get(node.node_id, []):
                if killed and not edge.source_before_write:
                    continue
                completions = state.completions
                if edge.completion_pop:
                    if not completions or completions[-1] not in edge.completion_pop:
                        continue
                    completions = completions[:-1]
                if edge.completion_push:
                    completions = (*completions, edge.completion_push)
                if (*state.nodes, node.node_id).count(edge.target) >= self.max_visits:
                    truncated = True
                    gaps.add("loop_unrolling_limit")
                    continue
                assertions, conditions = (*state.assertions, *([] if edge.source_before_write else domains)), state.conditions
                branch_reasons = list(state.reasons if edge.source_before_write else reasons)
                branch_env = dict(state.values if edge.source_before_write else env)
                branch_gaps = dict(state.value_gaps if edge.source_before_write else value_gaps)
                active_gaps = branch_gaps
                if edge.branch in {"true", "false"}:
                    expression = node.condition or ConditionExpression(reason="condition_not_extracted")
                    branch_domains: list[Any] = []
                    predicate = truth(evaluate(expression, node, branch_env, branch_reasons, branch_domains))
                    assertions = (*assertions, *branch_domains, predicate if edge.branch == "true" else z3.Not(predicate))
                    conditions = (*conditions, BranchConstraint(node_id=node.node_id, cfg_edge_id=edge.edge_id, branch=edge.branch, expression=expression,
                        location=node.location.model_dump() if node.location is not None else {}))
                if edge.branch == "exception":
                    branch_reasons.append("exception_path_not_modelled")
                # 无新增约束的普通边继承前缀结果，不为每条顺序边重复调用 SMT。
                outcome = z3.sat
                if len(assertions) != len(state.assertions):
                    solver = z3.Solver(ctx=context)
                    solver.set(timeout=max(1, int((deadline - monotonic()) * 1000)))
                    solver.add(*assertions)
                    outcome = solver.check()
                if outcome == z3.unsat:
                    unsat_nodes.update(item.node_id for item in conditions)
                    if not conflict_conditions:
                        conflict_conditions, conflict_edges = conditions, (*state.edges, edge.edge_id)
                    gaps.update(branch_reasons)
                    continue
                if outcome == z3.unknown:
                    branch_reasons.append("solver_unknown:" + solver.reason_unknown())
                pending.append(_State(edge.target, progress, branch_env, assertions, conditions, (*state.edges, edge.edge_id),
                                      (*state.nodes, node.node_id), tuple(dict.fromkeys(branch_reasons)), completions, branch_gaps))
        if unknown_witness is not None:
            gaps.update(unknown_witness.reasons)
        return base.model_copy(update={"status": "unknown" if truncated or gaps or unknown_witness else "infeasible_under_model",
            "explored_states": explored, "truncated": truncated, "reasons": sorted(gaps),
            "trace_kind": "witness" if unknown_witness else "conflicting_prefix" if conflict_conditions else "none",
            "cfg_edge_ids": list(unknown_witness.edges if unknown_witness else conflict_edges),
            "conditions": list(unknown_witness.conditions if unknown_witness else conflict_conditions),
            "unsat_condition_node_ids": sorted(unsat_nodes)})
