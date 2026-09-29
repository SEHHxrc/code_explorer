"""跨静态分析视图复用的语义事实契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CallableKind = Literal["function", "method", "constructor"]
CallRelation = Literal["calls", "instantiates"]
Certainty = Literal["must", "may"]
Confidence = Literal["high", "medium", "low"]


class SemanticLocation(BaseModel):
    """一处项目相对源码范围；零值表示旧前端未提供精确坐标。"""

    path: str
    line: int = Field(default=0, ge=0)
    column: int = Field(default=0, ge=0)
    end_line: int = Field(default=0, ge=0)
    end_column: int = Field(default=0, ge=0)
    location_id: str = ""


class CallableFact(BaseModel):
    """依赖分析直接观察到的函数、方法或构造器定义。"""

    symbol_id: str
    name: str
    kind: CallableKind
    language: str
    location: SemanticLocation
    return_type: str = ""
    provenance: Literal["observed"] = "observed"


class ResolvedTargetFact(BaseModel):
    """一个调用点的候选目标及其解析不确定性。"""

    edge_id: str
    target_symbol: str
    relation: CallRelation
    dispatch: str = "unknown"
    resolution_method: str = "unknown"
    certainty: Certainty = "may"
    confidence: Confidence = "low"
    truncated: bool = False
    provenance: Literal["inferred"] = "inferred"


class CallSiteFact(BaseModel):
    """与目标解析分离的一处调用表达式。"""

    callsite_id: str
    caller_symbol: str
    language: str
    name: str
    receiver: str = ""
    location: SemanticLocation
    targets: list[ResolvedTargetFact] = Field(default_factory=list)
    unresolved_reason: str | None = None
    truncated: bool = False
    provenance: Literal["observed"] = "observed"


class VariableTypeFact(BaseModel):
    """作用域内变量的声明或保守推断类型。"""

    fact_id: str
    owner_symbol: str
    name: str
    language: str
    type_literal: str
    provenance: Literal["observed", "inferred"] = "observed"
    confidence: Literal["high", "medium", "low"] = "high"


class ParameterFact(BaseModel):
    """ProgramGraph 观察到的一个可调用对象形参及其抽象槽位。"""

    fact_id: str
    slot_id: str
    callable_symbol: str
    method_id: str
    name: str
    position: int = Field(ge=0)
    language: str
    type_literal: str = ""
    location: SemanticLocation
    provenance: Literal["observed"] = "observed"


class ReturnFact(BaseModel):
    """函数内一处 return 语句的值来源及公共返回槽位。"""

    fact_id: str
    slot_id: str
    callable_symbol: str
    method_id: str
    node_id: str
    language: str
    location: SemanticLocation
    value_names: list[str] = Field(default_factory=list)
    callsite_ids: list[str] = Field(default_factory=list)
    certainty: Literal["must", "may"] = "may"
    provenance: Literal["observed"] = "observed"


class ValueSlotFact(BaseModel):
    """形参、函数返回或调用结果使用的跨图值接口。"""

    slot_id: str
    kind: Literal["parameter", "return", "call_result"]
    owner_symbol: str
    method_id: str = ""
    callsite_id: str = ""
    node_id: str = ""
    name: str = ""
    position: int | None = Field(default=None, ge=0)
    value_names: list[str] = Field(default_factory=list)
    location: SemanticLocation
    certainty: Literal["must", "may"] = "must"
    provenance: Literal["inferred"] = "inferred"


class SemanticIndexCoverage(BaseModel):
    """索引事实数量及无法精确表达的覆盖缺口。"""

    callable_count: int = Field(default=0, ge=0)
    callsite_count: int = Field(default=0, ge=0)
    resolved_target_count: int = Field(default=0, ge=0)
    unresolved_callsite_count: int = Field(default=0, ge=0)
    variable_type_count: int = Field(default=0, ge=0)
    parameter_count: int = Field(default=0, ge=0)
    return_count: int = Field(default=0, ge=0)
    value_slot_count: int = Field(default=0, ge=0)


class SemanticIndexArtifact(BaseModel):
    """依赖图、ProgramGraph 和安全分析共享的只读语义事实索引。"""

    schema_version: Literal["1.0", "1.1"] = "1.1"
    callables: dict[str, CallableFact] = Field(default_factory=dict)
    callsites: dict[str, CallSiteFact] = Field(default_factory=dict)
    variable_types: dict[str, VariableTypeFact] = Field(default_factory=dict)
    parameters: dict[str, ParameterFact] = Field(default_factory=dict)
    returns: dict[str, ReturnFact] = Field(default_factory=dict)
    value_slots: dict[str, ValueSlotFact] = Field(default_factory=dict)
    coverage: SemanticIndexCoverage = Field(default_factory=SemanticIndexCoverage)
    limitations: list[str] = Field(default_factory=list)
