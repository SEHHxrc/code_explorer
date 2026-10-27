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
    VariableRole,
    VariableDeclarationFact,
    VariableReferenceFact,
    SSAFunctionFact,
    BaseTypeFact,
    TypeFact,
)
from .hierarchy import TypeHierarchy
from .index import SemanticIndex
from .calls import CallTransition, CallTransitionIndex
from .protocols import CallTransitionView, SemanticCallSiteView, SemanticIndexView, SemanticReferenceView, SemanticTypeView, SemanticValueFlowView, SemanticSSAView
from .resolution import DeclarationSymbolIndex, ProjectSemanticResolver, ProjectSemanticResult
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
    "CallTransition",
    "CallTransitionIndex",
    "CallTransitionView",
    "CallableFact",
    "BaseTypeFact",
    "TypeFact",
    "TypeHierarchy",
    "DependencyAnalysisSemanticProvider",
    "DeclarationSymbolIndex",
    "ParameterFact",
    "ProgramGraphSemanticProvider",
    "ProjectSemanticResolver",
    "ProjectSemanticResult",
    "ResolvedTargetFact",
    "ReturnFact",
    "SemanticIndex",
    "SemanticCallSiteView",
    "SemanticIndexArtifact",
    "SemanticIndexCoverage",
    "SemanticIndexEnricher",
    "SemanticIndexProvider",
    "SemanticIndexView",
    "SemanticLocation",
    "SemanticValueFlowView",
    "SemanticSSAView",
    "SemanticTypeView",
    "SemanticReferenceView",
    "SSAFunctionFact",
    "ValueSlotFact",
    "VariableTypeFact",
    "VariableRole",
    "VariableDeclarationFact",
    "VariableReferenceFact",
    "call_result_slot_id",
    "parameter_slot_id",
    "return_slot_id",
]
