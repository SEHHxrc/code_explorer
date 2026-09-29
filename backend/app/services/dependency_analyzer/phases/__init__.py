"""静态分析流水线的阶段 mixin。"""

from __future__ import annotations

from .collection import CollectionPhase
from .graph import GraphResolutionPhase
from .imports import ImportResolutionPhase
from .indexing import IndexingPhase
from .types import TypeResolutionPhase

__all__ = [
    "CollectionPhase",
    "GraphResolutionPhase",
    "ImportResolutionPhase",
    "IndexingPhase",
    "TypeResolutionPhase",
]
