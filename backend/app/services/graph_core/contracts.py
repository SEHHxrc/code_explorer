"""不同静态分析图视图共享的轻量值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

GraphKind = Literal["dependency_graph", "program_graph", "security_flow"]


class GraphCapabilities(BaseModel):
    """显式描述一个图视图支持的结构和分析能力。"""

    hierarchical: bool = False
    cross_file: bool = False
    interprocedural: bool = False
    control_flow: bool = False
    data_flow: bool = False
    multigraph: bool = False
    function_partitioned: bool = False
    source_locations: bool = True


@dataclass(frozen=True)
class GraphNodeRef:
    """通用校验和遍历所需的最小节点投影。"""

    node_id: str
    kind: str


@dataclass(frozen=True)
class GraphEdgeRef:
    """通用校验和遍历所需的最小边投影。"""

    edge_id: str
    source: str
    target: str
    kind: str


class GraphValidationIssue(BaseModel):
    """不绑定具体图实现的结构校验问题。"""

    code: Literal["duplicate_node", "duplicate_edge", "dangling_edge", "empty_identity"]
    message: str
    node_id: str | None = None
    edge_id: str | None = None

