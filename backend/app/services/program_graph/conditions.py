"""公共条件、控制依赖和路径核验契约；不携带语言 AST 或漏洞结论。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from backend.app.services.value_binding.expressions import ConditionExpression


class ControlDependenceSummary(BaseModel):
    """针对实际 CFG 的支配树；虚拟后出口以空父 ID 表示，不能冒充源码出口。"""

    algorithm: Literal["postdom_edge_walk_v1"] = "postdom_edge_walk_v1"
    immediate_dominators: dict[str, str] = Field(default_factory=dict)
    immediate_postdominators: dict[str, str] = Field(default_factory=dict)
    unreachable_nodes: list[str] = Field(default_factory=list)
    non_exit_reaching_nodes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class BranchConstraint(BaseModel):
    """一次核验实际经过的分支；节点与 CFG 边 ID 可回溯精确源码位置。"""

    node_id: str
    cfg_edge_id: str
    branch: Literal["true", "false"]
    expression: ConditionExpression
    location: dict[str, str | int] = Field(default_factory=dict)
    frame_id: str = ""


class ConditionPathBinding(BaseModel):
    """既有边界确认的单值复制；不从污点相关性推断值相等。"""

    source_frame: str
    source_node_id: str
    source_name: str
    target_name: str
    target_node_id: str = ""
    reads_input: bool = True
    literal: ConditionExpression | None = None
    expression: ConditionExpression | None = None


class ConditionPathSegment(BaseModel):
    """共同路线中一个函数激活片段的可审计索引，不持久化完整控制图。"""

    method_id: str
    frame_id: str
    start_node_id: str
    end_node_id: str
    bindings: list[ConditionPathBinding] = Field(default_factory=list)


class PathConditionProof(BaseModel):
    """具体函数内/已选跨过程路线的有界核验；未知边界不得冒充闭合或实际可利用性证明。"""

    status: Literal["not_checked", "satisfiable_under_model", "infeasible_under_model", "unknown"] = "not_checked"
    scope: Literal["intra_procedural", "interprocedural"] = "intra_procedural"
    solver: str = "z3.scalar-path.v1"
    method_id: str = ""
    identity_basis: Literal["compiler", "partial_compiler", "lexical"] = "lexical"
    waypoint_node_ids: list[str] = Field(default_factory=list)
    target_condition: ConditionExpression | None = None
    cfg_edge_ids: list[str] = Field(default_factory=list)
    trace_kind: Literal["none", "witness", "conflicting_prefix"] = "none"
    conditions: list[BranchConstraint] = Field(default_factory=list)
    explored_states: int = 0
    truncated: bool = False
    reasons: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unsat_condition_node_ids: list[str] = Field(default_factory=list)
    segments: list[ConditionPathSegment] = Field(default_factory=list)
    limitation: str = "仅核验给定 CFG 与标量表达式模型；SAT 不等于实际执行或漏洞成立，UNSAT 不等于项目安全。"


class PredicateImplicationProof(BaseModel):
    """先排除不可达造成的空真，再用反例查询核验给定使用点的必要条件。"""

    status: Literal["implied_under_model", "counterexample_under_model", "unknown"]
    required_condition: ConditionExpression
    reachability: PathConditionProof
    counterexample: PathConditionProof | None = None
