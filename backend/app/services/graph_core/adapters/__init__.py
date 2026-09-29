"""现有图产物到 GraphArtifactView 的兼容适配器。"""

from .dependency import DependencyGraphView
from .program import ProgramGraphView

__all__ = ["DependencyGraphView", "ProgramGraphView"]

