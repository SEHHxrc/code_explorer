"""访问路径旧入口；公共实现位于 value_binding，不保留图专属副本。"""

from backend.app.services.value_binding.access_paths import (
    AccessPath, AccessPathResolver, AccessPathSegment, AccessPathSegmentKind,
    StaticIndex, normalize_static_index, parse_static_index_literal, render_access_path,
)

__all__ = ["AccessPath", "AccessPathResolver", "AccessPathSegment", "AccessPathSegmentKind",
           "StaticIndex", "normalize_static_index", "parse_static_index_literal", "render_access_path"]
