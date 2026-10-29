"""跨语言最小程序图的持久化契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.services.value_binding import BindingPattern, BindingValue
from backend.app.services.value_binding.contracts import CallableAccess
from backend.app.services.value_binding.parameters import ParameterKind
from backend.app.services.value_binding.returns import ReturnBinding
from backend.app.services.value_binding.objects import StorageValueSlot
from backend.app.services.value_binding.collections import CollectionAllocation
from backend.app.services.value_binding.selectors import SelectorExpression
from backend.app.services.value_binding.storage import StorageAllocation
from .conditions import ConditionExpression, ControlDependenceSummary

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
ReturnValueForm = Literal["reference", "call", "other"]
ObjectAssignmentKind = Literal["reference", "value", "unknown"]


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
    argument_values: list[BindingValue] = Field(default_factory=list)
    positional_conditions: list[ConditionExpression] = Field(default_factory=list)
    keyword_conditions: dict[str, ConditionExpression] = Field(default_factory=dict)
    positional_spread_positions: list[int] = Field(default_factory=list)
    result_targets: list[str] = Field(default_factory=list, description="有序返回接收槽；空字符串表示显式丢弃，不能压缩位置或当成变量。")
    positional_argument_locations: list[ProgramGraphLocation] = Field(default_factory=list)
    keyword_argument_locations: dict[str, ProgramGraphLocation] = Field(default_factory=dict)
    positional_references: list[str] = Field(default_factory=list)
    addressed_argument_positions: list[int] = Field(default_factory=list, description="源码显式取址的实参位置；区分传入指针值和取得变量存储地址。")
    keyword_references: dict[str, str] = Field(default_factory=dict)
    result_variable: str = ""
    receiver_field_bindings: dict[str, dict[str, str]] | None = None
    argument_field_bindings: dict[str, dict[str, str]] = Field(default_factory=dict)
    result_pattern: BindingPattern | None = None
    receiver_alias_origins: list[FieldValueOrigin] = Field(default_factory=list)
    receiver_allocation_id: str = ""
    initializer_kind: Literal["", "base", "same"] = ""
    object_effect_applied: bool = False  # 私有编译标记，避免工厂与引用写回交替处理时重复生成后置节点。
    object_effect_arguments: list[str] = Field(default_factory=list)
    object_effect_receiver: bool = False
    collection_effect_model: str = ""  # 私有编译模型版本；空值表示未核实，不能按同名方法放行。
    callable_access: CallableAccess | None = None
    argument_binding_error: str = ""


class ProgramValueTransfer(BaseModel):
    """一个节点内部从若干输入名字到单个输出名字的值变换。"""

    output_variable: str
    input_variables: list[str] = Field(default_factory=list)
    transfer_kind: ValueTransferKind
    callsite_ids: list[str] = Field(default_factory=list)
    certainty: EdgeCertainty = "must"
    reference_source: str = Field(default="", description="AST 确认的直接名称/纯字段引用 RHS；私有 Overlay 可由已验证构造器摘要生成，空值不证明引用关系。")
    storage_mode: Literal["", "reference", "address", "value_copy"] = ""
    weak_update: bool = False  # 目的槽可能没有被写入；与输入→输出关系的 may 置信度不同。

    provenance: Literal["observed", "inferred"] = "observed"


class InstanceMethodContext(BaseModel):
    """前端观察到的实例方法身份；不推断堆对象或执行调用目标解析。"""

    owner_symbol: str
    receiver: str
    is_constructor: bool = False
    shared_fields: list[str] = Field(default_factory=list)


class FieldValueOrigin(BaseModel):
    """字段摘要使用的原始局部赋值步骤，保留真实位置与值映射。"""

    symbol: str
    location: ProgramGraphLocation
    expression: str
    input_names: list[str] = Field(default_factory=list)
    output_names: list[str] = Field(default_factory=list)
    reference_edge_id: str | None = None
    callsite_id: str | None = None
    object_type_symbol: str | None = None


class InstanceFieldEffect(BaseModel):
    """私有对象 Overlay 的一次分配及参数到字段映射；非整对象污点。"""

    allocation_id: str
    node_id: str
    callsite_id: str
    reference_edge_id: str
    constructor_symbol: str
    parameter: str
    field_name: str
    output_name: str
    input_names: list[str] = Field(default_factory=list)
    location: ProgramGraphLocation
    origins: list[FieldValueOrigin] = Field(default_factory=list)
    object_type_symbol: str | None = None


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
    return_values: list[list[str]] = Field(default_factory=list)
    return_value_locations: list[ProgramGraphLocation] = Field(default_factory=list)
    return_value_forms: list[ReturnValueForm] = Field(default_factory=list)
    return_binding: ReturnBinding | None = None
    definition_declarations: dict[str, str] = Field(default_factory=dict)
    use_declarations: dict[str, str] = Field(default_factory=dict)
    binding_gaps: dict[str, str] = Field(default_factory=dict)
    definition_value_ids: dict[str, str] = Field(default_factory=dict)
    use_value_ids: dict[str, str] = Field(default_factory=dict)
    definition_object_slots: dict[str, StorageValueSlot] = Field(default_factory=dict)
    use_object_slots: dict[str, StorageValueSlot] = Field(default_factory=dict)
    object_alias_origins: dict[str, list[FieldValueOrigin]] = Field(default_factory=dict)
    provenance: Literal["observed", "inferred", "generated"] = "observed"
    certainty: EdgeCertainty = "must"
    storage_allocations: dict[str, StorageAllocation] = Field(default_factory=dict)
    collection_allocations: dict[str, CollectionAllocation] = Field(default_factory=dict)
    selector_definitions: dict[str, SelectorExpression] = Field(default_factory=dict)
    access_selectors: dict[str, SelectorExpression] = Field(default_factory=dict)
    selector_diagnostics: list[str] = Field(default_factory=list)
    condition: ConditionExpression | None = None
    scalar_values: dict[str, ConditionExpression] = Field(default_factory=dict)
    storage_invalidations: list[str] = Field(default_factory=list, description="私有传播视图的未知副作用前沿；只失效已知引用，不证明该存储已被安全覆盖。")
    object_reference_bindings: dict[str, list[str]] = Field(default_factory=dict, description="私有操作正常完成后的引用写入：展示槽→已核实静态分配集合；空字符串保留未知，不来自模型。")


class ProgramGraphEdge(BaseModel):
    """CFG、到达定义或值流 Overlay 中的一条有类型边。"""

    edge_id: str
    source: str
    target: str
    kind: Literal["cfg", "reaching_def", "value_flow", "control_dependence"]
    branch: ProgramEdgeBranch | None = None
    variable: str | None = None
    source_variable: str | None = None
    target_variable: str | None = None
    read_variable: str | None = None
    transfer_kind: ValueTransferKind | None = None
    callsite_ids: list[str] = Field(default_factory=list)
    source_value_id: str = ""
    target_value_id: str = ""
    read_value_id: str = ""
    certainty: EdgeCertainty = "must"
    provenance: Literal["observed", "inferred"] = "inferred"
    completion_push: str = ""
    completion_pop: list[str] = Field(default_factory=list)
    source_before_write: bool = False
    loop_back: bool = False


class ValueBackendSelection(BaseModel):
    """单函数值查询的实际来源；声明绑定和 SSA 求解明确区分，旧产物不补造选择。"""

    backend: Literal["common_cfg", "compiler_bound_cfg", "native_ssa_def_use"]
    identity_backend: Literal["lexical", "compiler", "partial_compiler", "source_location"]
    fallback_reason: str = ""
    native_symbol: str = ""
    binding_gaps: list[str] = Field(default_factory=list)


class FunctionProgramGraph(BaseModel):
    """可独立加载和重新计算的单函数程序图分区。"""

    method_id: str
    symbol_id: str
    language: str
    name: str
    location: ProgramGraphLocation
    parameters: list[str] = Field(default_factory=list)
    parameter_patterns: list[BindingPattern] = Field(default_factory=list)
    parameter_kinds: dict[str, ParameterKind] = Field(default_factory=dict)
    entry_node_id: str
    exit_node_id: str
    nodes: dict[str, ProgramGraphNode] = Field(default_factory=dict)
    edges: list[ProgramGraphEdge] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    instance_context: InstanceMethodContext | None = None
    deferred_return: bool = False
    field_effects: list[InstanceFieldEffect] = Field(default_factory=list)
    instance_allocations: dict[str, str] = Field(default_factory=dict)
    object_assignment: ObjectAssignmentKind = "unknown"
    normal_exit_writes_modelled: bool = False
    has_body: bool = True
    storage_parameters: dict[str, Literal["value", "reference"]] = Field(default_factory=dict)
    value_backend: ValueBackendSelection | None = None
    control_dependence: ControlDependenceSummary | None = None
    parameter_defaults: dict[str, ConditionExpression] = Field(default_factory=dict)


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
    control_dependence_edge_count: int = Field(default=0, ge=0)


class ProgramGraphArtifact(BaseModel):
    """按函数分区、按 Overlay 标记的最小跨语言程序图。"""

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9", "1.10", "1.11", "1.12", "1.13", "1.14", "1.15", "1.16", "1.17", "1.18", "1.19", "1.20"] = "1.20"
    graph_kind: Literal["minimal_cpg"] = "minimal_cpg"
    overlays: list[str] = Field(
        default_factory=lambda: [
            "base", "cfg", "reaching_definitions", "value_flow", "control_dependence",
        ],
    )
    languages: list[str] = Field(default_factory=list)
    functions: dict[str, FunctionProgramGraph] = Field(default_factory=dict)
    coverage: ProgramGraphCoverage = Field(default_factory=ProgramGraphCoverage)
    failures: list[dict[str, object]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
