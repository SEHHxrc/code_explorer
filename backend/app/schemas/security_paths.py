"""静态安全路径可视化的白名单交换契约；不是完整 CFG/DFG 或漏洞报告。"""

from typing import Literal

from pydantic import BaseModel, Field

from .dependency_graph import DependencyGraphDTO


class PathLocationDTO(BaseModel):
    """项目相对源码位置；不返回宿主路径或源码正文。"""

    path: str = Field(min_length=1, max_length=1000)
    line: int = Field(ge=1)
    column: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=1)


class PathEndpointDTO(BaseModel):
    """路径端点的规则观察；Source/Sink 身份不等于攻击或漏洞成立。"""

    name: str = Field(max_length=500)
    symbol: str = Field(max_length=1000)
    location: PathLocationDTO | None = None
    trust_class: str = Field(max_length=80)


class SecurityPathDTO(BaseModel):
    """一条已保存候选的函数级投影；相同节点对可对应多条不同路线。"""

    id: str
    path_group_id: str
    kind: Literal["propagation_path", "structural_lead", "operation_lead"]
    category: str
    confidence: str
    source: PathEndpointDTO
    sink: PathEndpointDTO
    node_ids: list[str]
    edge_ids: list[str]
    step_count: int
    path_status: str
    condition_statuses: list[str] = Field(default_factory=list)
    unresolved_count: int = 0
    truncated: bool = False
    projection_missing: int = 0
    possible_edge_ids: list[str] = Field(default_factory=list)
    call_connection_counts: dict[str, int] = Field(default_factory=dict)


class SecurityBoundaryDTO(BaseModel):
    """可定位的传播缺口；只有已保存事实，不声称缺口两侧已经相连。"""

    id: str
    symbol: str
    location: PathLocationDTO | None = None
    reasons: list[str] = Field(default_factory=list)


class SecurityOperationDTO(BaseModel):
    """未配对 Source 的已保存 Sink：只有操作定位，不补造路径族、传播步骤或连线。"""

    id: str
    kind: Literal["unpaired_operation"] = "unpaired_operation"
    category: str
    rule_id: str
    severity: Literal["informational", "low", "medium", "high"]
    sink: PathEndpointDTO
    node_id: str = Field(max_length=1000)
    projection_missing: int = 0
    context_node_ids: list[str] = Field(default_factory=list)
    context_edge_ids: list[str] = Field(default_factory=list)
    boundary_ids: list[str] = Field(default_factory=list)


class SecurityPathGraphDTO(BaseModel):
    """候选并集及独立操作；二者分开计数，展示截断不等同分析覆盖，零路径不等同安全。"""

    schema_version: Literal["1.4"] = "1.4"
    available: bool
    completed: bool
    graph: DependencyGraphDTO
    paths: list[SecurityPathDTO] = Field(default_factory=list)
    operations: list[SecurityOperationDTO] = Field(default_factory=list)
    candidate_count: int = 0
    additional_dataflow_count: int = 0
    unavailable_dataflows: int = 0
    unavailable_candidates: int = 0
    unpaired_sink_count: int = 0
    kind_counts: dict[str, int] = Field(default_factory=dict)
    analysis_truncated: bool = False
    scan_failure_count: int = 0
    parse_failure_count: int = 0
    files_scanned: int = 0
    files_considered: int = 0
    unsupported_languages: list[str] = Field(default_factory=list)
    structural_edge_ids: list[str] = Field(default_factory=list)
    possible_edge_ids: list[str] = Field(default_factory=list)
    boundaries: list[SecurityBoundaryDTO] = Field(default_factory=list)
    structure_truncated: bool = False


class SecurityPathStepDTO(BaseModel):
    """按原产物顺序保留的定位与值名；不从相邻步骤臆造调用边。"""

    kind: str
    symbol: str
    location: PathLocationDTO | None = None
    uses: list[str] = Field(default_factory=list)
    defines: list[str] = Field(default_factory=list)
    certainty: str = "unknown"


class SecurityCallConnectionDTO(BaseModel):
    """按需展示真实调用点的目标依据；不推断缺口另一端，不提供完整图。"""

    call_edge_id: str
    source: str
    target: str
    status: Literal["modelled", "possible", "unresolved"]
    target_certainty: Literal["must", "may", "unresolved"]
    confidence: Literal["high", "medium", "low"]
    resolution_method: str = Field(max_length=80)
    location: PathLocationDTO | None = None
    truncated: bool = False
    speculative_target: bool = False


class SecurityPathDetailDTO(BaseModel):
    """按需展开路线；防护仅记录观察位置，不冒充已证明有效。"""

    id: str
    steps: list[SecurityPathStepDTO] = Field(default_factory=list)
    guards: list[PathEndpointDTO] = Field(default_factory=list)
    sanitizers: list[PathEndpointDTO] = Field(default_factory=list)
    value_boundary_count: int = 0
    unavailable_locations: int = 0
    boundaries: list[SecurityBoundaryDTO] = Field(default_factory=list)
    call_connections: list[SecurityCallConnectionDTO] = Field(default_factory=list)
