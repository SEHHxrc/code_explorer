"""静态分析视图共享的语义事实索引。"""

from .contracts import (
    CallableFact,
    CallSiteFact,
    ParameterFact,
    ResolvedTargetFact,
    ReturnFact,
    SemanticIndexArtifact,
    SemanticIndexCoverage,
    SemanticLocation,
    ValueSlotFact,
    VariableTypeFact,
)
from .index import SemanticIndex
from .protocols import SemanticIndexView, SemanticValueFlowView
from .provider import (
    DependencyAnalysisSemanticProvider,
    ProgramGraphSemanticProvider,
    SemanticIndexEnricher,
    SemanticIndexProvider,
)
from .slots import (
    CALL_RESULT_SLOT,
    PARAMETER_SLOT,
    RETURN_SLOT,
    call_result_slot_id,
    parameter_slot_id,
    return_slot_id,
)

__all__ = [
    "CALL_RESULT_SLOT",
    "PARAMETER_SLOT",
    "RETURN_SLOT",
    "CallSiteFact",
    "CallableFact",
    "DependencyAnalysisSemanticProvider",
    "ParameterFact",
    "ProgramGraphSemanticProvider",
    "ResolvedTargetFact",
    "ReturnFact",
    "SemanticIndex",
    "SemanticIndexArtifact",
    "SemanticIndexCoverage",
    "SemanticIndexEnricher",
    "SemanticIndexProvider",
    "SemanticIndexView",
    "SemanticLocation",
    "SemanticValueFlowView",
    "ValueSlotFact",
    "VariableTypeFact",
    "call_result_slot_id",
    "parameter_slot_id",
    "return_slot_id",
]
