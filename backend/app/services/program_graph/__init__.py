"""跨语言最小 CPG/程序图公共基础设施。"""

from .contracts import (
    FunctionProgramGraph,
    ProgramCallSite,
    ProgramGraphArtifact,
    ProgramGraphCoverage,
    ProgramGraphEdge,
    ProgramGraphLocation,
    ProgramGraphNode,
    ProgramValueTransfer,
    ValueTransferKind,
    ValueBackendSelection,
)
from .registry import ProgramGraphFrontendRegistry
from .service import ProgramGraphService
from .value_query import ValueFlowQuery
from .conditions import ConditionExpression, PathConditionProof, PredicateImplicationProof
from .path_conditions import PathConditionQuery
from .path_composition import ConditionRoutePart, ConditionRouteQuery

__all__ = [
    "FunctionProgramGraph",
    "ProgramCallSite",
    "ProgramGraphArtifact",
    "ProgramGraphCoverage",
    "ProgramGraphEdge",
    "ProgramGraphFrontendRegistry",
    "ProgramGraphLocation",
    "ProgramGraphNode",
    "ProgramGraphService",
    "ProgramValueTransfer",
    "ValueTransferKind",
    "ValueBackendSelection",
    "ValueFlowQuery",
    "ConditionExpression",
    "PathConditionProof",
    "PredicateImplicationProof",
    "PathConditionQuery",
    "ConditionRoutePart",
    "ConditionRouteQuery",
]
