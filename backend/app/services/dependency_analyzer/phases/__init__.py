# -*- coding: utf-8 -*-
from __future__ import annotations

"""静态分析流水线的阶段 mixin。"""
from .collection import CollectionPhase
from .graph import GraphResolutionPhase
from .imports import ImportResolutionPhase
from .indexing import IndexingPhase
from .types import TypeResolutionPhase

__all__ = [
    "CollectionPhase", "IndexingPhase", "ImportResolutionPhase",
    "TypeResolutionPhase", "GraphResolutionPhase",
]
