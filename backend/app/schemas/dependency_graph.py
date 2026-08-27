"""依赖图的后端到前端交换契约。"""

from typing import Literal

from pydantic import BaseModel, Field


GraphScope = Literal["project", "builtin", "stdlib", "external"]


class GraphNodeDTO(BaseModel):
    """一个经过字段白名单和路径脱敏的依赖图节点。"""

    id: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="variable", max_length=80)
    level: str = Field(default="variable", max_length=80)
    scope: GraphScope = "project"
    file: str | None = Field(default=None, max_length=1000)
    line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    lang: str | None = Field(default=None, max_length=40)
    in_degree: int = Field(default=0, ge=0)
    out_degree: int = Field(default=0, ge=0)
    degree: int = Field(default=0, ge=0)


class GraphEdgeDTO(BaseModel):
    """一个端点有效、无自环且字段受限的有向依赖边。"""

    id: str = Field(min_length=1, max_length=2200)
    source: str = Field(min_length=1, max_length=1000)
    target: str = Field(min_length=1, max_length=1000)
    relation: str = Field(default="calls", max_length=80)
    dispatch: str | None = Field(default=None, max_length=80)
    dynamic: bool = False


class GraphSummaryDTO(BaseModel):
    """供前端快速渲染概况而无需再次扫描整张图的统计信息。"""

    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    level_counts: dict[str, int] = Field(default_factory=dict)
    relation_counts: dict[str, int] = Field(default_factory=dict)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    max_degree: int = Field(default=0, ge=0)
    min_degree: int = Field(default=0, ge=0)
    truncated: bool = False


class DependencyGraphDTO(BaseModel):
    """稳定、可版本化的依赖图 API 交换对象。"""

    schema_version: Literal["1.0"] = "1.0"
    nodes: list[GraphNodeDTO] = Field(default_factory=list)
    edges: list[GraphEdgeDTO] = Field(default_factory=list)
    summary: GraphSummaryDTO
    warnings: list[str] = Field(default_factory=list)