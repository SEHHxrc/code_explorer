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
)
from .registry import ProgramGraphFrontendRegistry
from .service import ProgramGraphService

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
]
