"""跨语言最小程序图的持久化契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProgramNodeKind = Literal[
    "method_entry", "method_exit", "operation", "assignment", "call",
    "condition", "return", "throw", "break", "continue", "try",
]
ProgramEdgeBranch = Literal[
    "normal", "true", "false", "back", "break", "continue",
    "return", "exception",
]
EdgeCertainty = Literal["must", "may"]
ValueTransferKind = Literal[
    "passthrough", "assignment", "expression", "call_result",
]


class ProgramGraphLocation(BaseModel):
    """程序图节点对应的项目相对源码范围。"""

    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=1)
    location_id: str


class ProgramCallSite(BaseModel):
    """嵌入操作节点、可与依赖调用边对齐的一处调用表达式。"""

    callsite_id: str
    name: str
    receiver: str = ""
    location: ProgramGraphLocation
    positional_arguments: list[list[str]] = Field(default_factory=list)
    keyword_arguments: dict[str, list[str]] = Field(default_factory=dict)
    receiver_identifiers: list[str] = Field(default_factory=list)


class ProgramValueTransfer(BaseModel):
    """一个节点内部从若干输入名字到单个输出名字的值变换。"""

    output_variable: str
    input_variables: list[str] = Field(default_factory=list)
    transfer_kind: ValueTransferKind
    callsite_ids: list[str] = Field(default_factory=list)
    certainty: EdgeCertainty = "must"
    provenance: Literal["observed", "inferred"] = "observed"


class ProgramGraphNode(BaseModel):
    """函数分区中的一个语言无关操作或控制节点。"""

    node_id: str
    kind: ProgramNodeKind
    language: str
    method_id: str
    location: ProgramGraphLocation | None = None
    code: str = ""
    definitions: list[str] = Field(default_factory=list)
    uses: list[str] = Field(default_factory=list)
    calls: list[ProgramCallSite] = Field(default_factory=list)
    value_transfers: list[ProgramValueTransfer] = Field(default_factory=list)
    provenance: Literal["observed", "generated"] = "observed"


class ProgramGraphEdge(BaseModel):
    """CFG、到达定义或值流 Overlay 中的一条有类型边。"""

    edge_id: str
    source: str
    target: str
    kind: Literal["cfg", "reaching_def", "value_flow"]
    branch: ProgramEdgeBranch | None = None
    variable: str | None = None
    source_variable: str | None = None
    target_variable: str | None = None
    transfer_kind: ValueTransferKind | None = None
    callsite_ids: list[str] = Field(default_factory=list)
    certainty: EdgeCertainty = "must"
    provenance: Literal["observed", "inferred"] = "inferred"


class FunctionProgramGraph(BaseModel):
    """可独立加载和重新计算的单函数程序图分区。"""

    method_id: str
    symbol_id: str
    language: str
    name: str
    location: ProgramGraphLocation
    parameters: list[str] = Field(default_factory=list)
    entry_node_id: str
    exit_node_id: str
    nodes: dict[str, ProgramGraphNode] = Field(default_factory=dict)
    edges: list[ProgramGraphEdge] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ProgramGraphCoverage(BaseModel):
    """公共程序图构建的文件与函数覆盖统计。"""

    files_considered: int = Field(default=0, ge=0)
    files_parsed: int = Field(default=0, ge=0)
    parse_failures: int = Field(default=0, ge=0)
    function_count: int = Field(default=0, ge=0)
    cfg_node_count: int = Field(default=0, ge=0)
    cfg_edge_count: int = Field(default=0, ge=0)
    reaching_def_edge_count: int = Field(default=0, ge=0)
    value_flow_edge_count: int = Field(default=0, ge=0)


class ProgramGraphArtifact(BaseModel):
    """按函数分区、按 Overlay 标记的最小跨语言程序图。"""

    schema_version: Literal["1.0", "1.1"] = "1.1"
    graph_kind: Literal["minimal_cpg"] = "minimal_cpg"
    overlays: list[str] = Field(
        default_factory=lambda: [
            "base", "cfg", "reaching_definitions", "value_flow",
        ],
    )
    languages: list[str] = Field(default_factory=list)
    functions: dict[str, FunctionProgramGraph] = Field(default_factory=dict)
    coverage: ProgramGraphCoverage = Field(default_factory=ProgramGraphCoverage)
    failures: list[dict[str, object]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
