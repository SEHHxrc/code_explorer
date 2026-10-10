"""面向大模型的紧凑静态安全证据协议。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .contracts import CodeSnippet, EvidenceLocation, SecurityEntrypoint


class LLMSecurityEndpoint(BaseModel):
    """LLM 判断漏洞所需的 Source 或 Sink 端点。"""

    fact_id: str
    rule_id: str
    role: Literal["source", "sink"]
    category: str
    name: str
    symbol: str
    location: EvidenceLocation
    trust_class: str
    provenance: Literal["observed", "inferred"]
    value_role: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class LLMFlowStep(BaseModel):
    """按执行或传播顺序呈现的一条必要路径步骤。"""

    order: int = Field(ge=0)
    kind: Literal["assignment", "call", "return"]
    location: EvidenceLocation | None = None
    symbol: str
    expression: str
    input_names: list[str] = Field(default_factory=list)
    output_names: list[str] = Field(default_factory=list)
    certainty: Literal["must", "may", "unresolved"]
    importance: Literal["essential", "important", "unimportant"] = "important"
    provenance: Literal["observed", "inferred"] = "inferred"


class LLMRelatedFact(BaseModel):
    """候选路径上的 Guard 或 Sanitizer。"""

    fact_id: str
    role: Literal["guard", "sanitizer"]
    category: str
    name: str
    symbol: str
    location: EvidenceLocation
    provenance: Literal["observed", "inferred"]


class LLMSecurityFinding(BaseModel):
    """供模型独立判断的一条自包含安全候选档案。"""

    finding_id: str
    fingerprint: str
    rule_id: str
    cwe: str | None = None
    category: str
    severity: str
    confidence: str
    confidence_dimensions: dict[str, str]
    claim: Literal[
        "intra_procedural_dataflow",
        "interprocedural_dataflow",
        "structural_reachability_only",
    ]
    taint_status: str
    source: LLMSecurityEndpoint
    sink: LLMSecurityEndpoint
    flow: list[LLMFlowStep] = Field(default_factory=list)
    guards: list[LLMRelatedFact] = Field(default_factory=list)
    sanitizers: list[LLMRelatedFact] = Field(default_factory=list)
    snippets: list[CodeSnippet] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    truncated: bool = False


class LLMSecurityObservation(BaseModel):
    """未形成候选路径的真实规则观察；不把单点匹配冒充漏洞或污点链。"""

    fact_id: str
    kind: Literal["source", "sink", "guard", "sanitizer"]
    rule_id: str
    category: str
    name: str
    symbol: str
    location: EvidenceLocation
    provenance: Literal["observed", "inferred"]
    trust_class: str
    claim: Literal["rule_match_only"] = "rule_match_only"
    snippet: CodeSnippet | None = None


class LLMSecurityEvidenceEnvelope(BaseModel):
    """不含完整依赖图、Repo Map 或系统生成推断的 LLM 输入。"""

    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    kind: Literal["static_security_evidence"] = "static_security_evidence"
    source_schema_version: str
    analysis: dict[str, Any]
    claim_semantics: dict[str, str]
    findings: list[LLMSecurityFinding] = Field(default_factory=list)
    entrypoints: list[SecurityEntrypoint] = Field(default_factory=list)
    observations: list[LLMSecurityObservation] = Field(default_factory=list)
    omitted_entrypoints: int = Field(default=0, ge=0)
    omitted_observations: int = Field(default=0, ge=0)
    omitted_findings: int = Field(default=0, ge=0)
    truncated: bool = False
