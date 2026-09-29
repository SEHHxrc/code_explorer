"""静态分析图共享的接口、能力、只读适配器和结构校验。"""

from .adapters import DependencyGraphView, ProgramGraphView
from .contracts import (
    GraphCapabilities,
    GraphEdgeRef,
    GraphKind,
    GraphNodeRef,
    GraphValidationIssue,
)
from .protocols import GraphArtifactView
from .validation import GraphViewValidator

__all__ = [
    "DependencyGraphView",
    "GraphArtifactView",
    "GraphCapabilities",
    "GraphEdgeRef",
    "GraphKind",
    "GraphNodeRef",
    "GraphValidationIssue",
    "GraphViewValidator",
    "ProgramGraphView",
]

