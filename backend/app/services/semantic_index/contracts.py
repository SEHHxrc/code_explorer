"""跨静态分析视图复用的语义事实契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CallableKind = Literal["function", "method", "constructor"]
CallRelation = Literal["calls", "instantiates"]
Certainty = Literal["must", "may"]
Confidence = Literal["high", "medium", "low"]
VariableRole = Literal["variable", "parameter", "unknown"]


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


class BaseTypeFact(BaseModel):
    """已解析的直接父类型；保留声明顺序和 C++ 虚基类，不把嵌入冒充继承。"""

    symbol_id: str
    relation: Literal["inherits", "implements", "embeds"] = "inherits"
    virtual: bool = False


class TypeFact(BaseModel):
    """项目类型声明与直属字段；属于符号解析，不判断安全性或推断运行时布局。"""

    symbol_id: str
    language: str
    kind: str
    bases: list[BaseTypeFact] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    unresolved_bases: list[str] = Field(default_factory=list)


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
    fallback_reason: str | None = None


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
    fallback_reason: str | None = None


class VariableTypeFact(BaseModel):
    """声明级类型事实；旧产物缺少声明身份和角色时不补造块级可见性。"""

    fact_id: str
    owner_symbol: str
    name: str
    language: str
    type_literal: str
    provenance: Literal["observed", "inferred"] = "observed"
    confidence: Literal["high", "medium", "low"] = "high"
    resolution_method: str = "syntax_or_heuristic"
    location: SemanticLocation | None = None
    declaration_id: str = ""
    role: VariableRole = "unknown"


class VariableDeclarationFact(BaseModel):
    """源码变量/形参声明实体，与运行时对象、赋值版本和安全角色无关。"""

    declaration_id: str
    owner_symbol: str
    name: str
    role: Literal["variable", "parameter"]
    language: str
    location: SemanticLocation
    owner_unresolved_reason: str | None = None


class VariableReferenceFact(BaseModel):
    """编译器确认的变量引用绑定；包括写引用，不代表值读取、到达定义或污点边。"""

    reference_id: str
    owner_symbol: str
    name: str
    language: str
    location: SemanticLocation
    declaration_ids: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None
    owner_unresolved_reason: str | None = None
    resolution_method: str
    provenance: Literal["inferred"] = "inferred"


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
    declaration_id: str = ""
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
    value_ids: dict[str, str] = Field(default_factory=dict)
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


class SSAValueFact(BaseModel):
    """原生 SSA 的函数内值身份；名字仅用于显示，不能代替值身份。"""

    kind: str
    name: str = ""
    type_literal: str = ""
    location: SemanticLocation | None = None


class SSAInstructionFact(BaseModel):
    """指令结果与有序操作数；φ 操作数顺序对应基本块的前驱顺序。"""

    opcode: str
    block: int = Field(ge=0)
    operands: list[str] = Field(default_factory=list)
    result: str | None = None
    location: SemanticLocation | None = None
    call_location: SemanticLocation | None = None
    source_name: str = ""
    call_arguments: list[str] = Field(default_factory=list)
    receiver_value: str | None = None
    invocation_kind: str = ""
    projection_index: int | None = Field(default=None, ge=0)
    operator: str = ""


class SSABlockFact(BaseModel):
    """编译器 CFG 基本块；保留前驱/后继顺序，不按行号重建控制流。"""

    predecessors: list[int] = Field(default_factory=list)
    successors: list[int] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class SSAFunctionFact(BaseModel):
    """语言无关的原生 SSA 交换层，不把操作数依赖误称为完整内存污点传播。"""

    name: str
    kind: Literal["function", "method"]
    location: SemanticLocation
    parameters: list[str] = Field(default_factory=list)
    receiver_parameter: str | None = None
    values: dict[str, SSAValueFact] = Field(default_factory=dict)
    instructions: dict[str, SSAInstructionFact] = Field(default_factory=dict)
    blocks: list[SSABlockFact] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class SemanticIndexArtifact(BaseModel):
    """依赖图、ProgramGraph 和安全分析共享的只读语义事实索引。"""

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"] = "1.1"
    types: dict[str, TypeFact] = Field(default_factory=dict)
    callables: dict[str, CallableFact] = Field(default_factory=dict)
    callsites: dict[str, CallSiteFact] = Field(default_factory=dict)
    variable_types: dict[str, VariableTypeFact] = Field(default_factory=dict)
    variable_declarations: dict[str, VariableDeclarationFact] = Field(default_factory=dict)
    variable_references: dict[str, VariableReferenceFact] = Field(default_factory=dict)
    parameters: dict[str, ParameterFact] = Field(default_factory=dict)
    returns: dict[str, ReturnFact] = Field(default_factory=dict)
    value_slots: dict[str, ValueSlotFact] = Field(default_factory=dict)
    coverage: SemanticIndexCoverage = Field(default_factory=SemanticIndexCoverage)
    limitations: list[str] = Field(default_factory=list)
    backend_runs: list[dict[str, object]] = Field(default_factory=list)
    native_ssa: dict[str, SSAFunctionFact] = Field(default_factory=dict)
