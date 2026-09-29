"""静态安全结构证据的持久化契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FactKind = Literal["source", "sink", "guard", "sanitizer"]
TrustClass = Literal[
    "untrusted", "external_configuration", "external_file",
    "framework_dependency", "internal", "unknown",
]
Severity = Literal["informational", "low", "medium", "high"]
Confidence = Literal["high", "medium", "low"]
TargetCertainty = Literal["must", "may", "unresolved"]
SnippetRole = Literal["source", "sink", "guard", "intermediate"]


class EvidenceLocation(BaseModel):
    """一处可回到源码验证的位置。"""

    path: str = Field(min_length=1, max_length=1000)
    line: int = Field(ge=1)
    column: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=1)
    location_id: str | None = None


class StaticSecurityFact(BaseModel):
    """静态规则直接观察到的 Source、Sink、Guard 或 Sanitizer。"""

    fact_id: str
    fact_kind: FactKind
    rule_id: str
    category: str
    name: str
    symbol: str
    location: EvidenceLocation
    cwe: str | None = None
    trust_class: TrustClass = "unknown"
    severity: Severity = "informational"
    provenance: Literal["observed", "inferred"] = "observed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecurityEntrypoint(BaseModel):
    """程序对外入口；入口本身不等于不可信数据 Source。"""

    entrypoint_id: str
    kind: Literal["http_route", "websocket", "cli", "consumer", "other"]
    framework: str
    symbol: str
    location: EvidenceLocation
    provenance: Literal["observed", "inferred"] = "observed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PathEdgeEvidence(BaseModel):
    """结构调用路径中的一条可追溯边。"""

    edge_id: str
    callsite_id: str
    source: str
    target: str
    relation: str = "calls"
    dispatch: str = "unknown"
    resolution_method: str = "unknown"
    target_certainty: TargetCertainty = "unresolved"
    confidence: Confidence = "low"
    truncated: bool = False
    unresolved_reason: str | None = None
    provenance: Literal["observed", "inferred"] = "inferred"
    callsite: EvidenceLocation | None = None


class CodeSnippet(BaseModel):
    """经过长度限制和敏感值遮蔽的最小源码片段。"""

    snippet_id: str
    role: SnippetRole
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str
    truncated: bool = False


class FlowStepEvidence(BaseModel):
    """一条有序 Def-Use 路径中的源码事实和推断关系。"""

    step_id: str
    order: int = Field(ge=0)
    kind: Literal["source", "assignment", "call", "return", "sanitizer", "sink"]
    symbol: str
    location: EvidenceLocation
    expression: str
    input_names: list[str] = Field(default_factory=list)
    output_names: list[str] = Field(default_factory=list)
    provenance: Literal["observed", "inferred"] = "observed"
    certainty: Literal["must", "may"] = "must"
    importance: Literal["essential", "important", "unimportant"] = "important"


class DataFlowEvidence(BaseModel):
    """静态分析得到的一条函数内或有界跨函数值传播路径。"""

    flow_id: str
    language: str
    scope: Literal["intra_procedural", "interprocedural"] = "intra_procedural"
    symbol: str
    source_fact_id: str
    sink_fact_id: str
    status: Literal["must_reach_sink", "may_reach_sink"]
    confidence: Literal["high", "medium", "low"]
    steps: list[FlowStepEvidence]
    call_edge_ids: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    truncated: bool = False


class SecurityCandidate(BaseModel):
    """只引用去重实体的安全结构候选，而非已确认漏洞。"""

    candidate_id: str
    rule_id: str
    cwe: str | None = None
    category: str
    status: Literal["candidate"] = "candidate"
    severity: Literal["informational", "low", "medium", "high"]
    confidence: Literal["high", "medium", "low"]
    path_kind: Literal[
        "structural_call_path",
        "intra_procedural_dataflow",
        "interprocedural_dataflow",
    ] = "structural_call_path"
    dataflow_verified: bool = False
    taint_status: Literal["not_analyzed", "must_reach_sink", "may_reach_sink"] = "not_analyzed"
    dataflow_id: str | None = None
    source_fact_id: str
    sink_fact_id: str
    call_edge_ids: list[str] = Field(default_factory=list)
    guard_fact_ids: list[str] = Field(default_factory=list)
    sanitizer_fact_ids: list[str] = Field(default_factory=list)
    snippet_ids: list[str] = Field(default_factory=list)
    unresolved_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    truncated: bool = False


class SecurityCoverage(BaseModel):
    """静态安全证据包的扫描范围和结果计数。"""

    files_considered: int = Field(default=0, ge=0)
    files_scanned: int = Field(default=0, ge=0)
    parse_failures: int = Field(default=0, ge=0)
    skipped_files: int = Field(default=0, ge=0)
    diagnostic_count: int = Field(default=0, ge=0)
    unsupported_language_count: int = Field(default=0, ge=0)
    entrypoint_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    sink_count: int = Field(default=0, ge=0)
    guard_count: int = Field(default=0, ge=0)
    sanitizer_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    dataflow_count: int = Field(default=0, ge=0)
    candidate_limit_reached: bool = False


class SecurityEvidencePack(BaseModel):
    """使用 ID 注册表去重且不含完整图和 Repo Map 的安全证据包。"""

    schema_version: Literal["2.1", "2.2", "2.3"] = "2.3"
    ir_version: Literal["1.2", "1.3"] = "1.3"
    analysis_kind: Literal["static_security_evidence"] = "static_security_evidence"
    rule_packs: list[str] = Field(default_factory=list)
    languages_analyzed: list[str] = Field(default_factory=list)
    unsupported_languages: list[str] = Field(default_factory=list)
    completed: bool = True
    dataflow_verified: bool = False
    entrypoints: dict[str, SecurityEntrypoint] = Field(default_factory=dict)
    facts: dict[str, StaticSecurityFact] = Field(default_factory=dict)
    call_edges: dict[str, PathEdgeEvidence] = Field(default_factory=dict)
    snippets: dict[str, CodeSnippet] = Field(default_factory=dict)
    dataflows: dict[str, DataFlowEvidence] = Field(default_factory=dict)
    candidates: list[SecurityCandidate] = Field(default_factory=list)
    coverage: SecurityCoverage = Field(default_factory=SecurityCoverage)
    scan_failures: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
