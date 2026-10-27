"""调用与声明共用的已知源码范围匹配；不推断词法可见性或使用点绑定。"""

from .contracts import SemanticLocation
from backend.app.services.program_index import ProgramIdentity


def matches_location(query: SemanticLocation, actual: SemanticLocation) -> bool:
    """匹配起始行及已知范围；零坐标未知，起始行缺失或位置身份冲突时不猜测。"""
    return bool(query.line and ProgramIdentity.file_id(query.path) == ProgramIdentity.file_id(actual.path)
                and query.line == actual.line
                and (not query.location_id or query.location_id == actual.location_id)
                and all(not getattr(query, field) or getattr(actual, field) == getattr(query, field)
                        for field in ("column", "end_line", "end_column")))


def normalized_location(location: SemanticLocation) -> SemanticLocation:
    """规范相对路径并派生范围身份；零坐标仍为未知，不证明声明范围完整。"""
    return location.model_copy(update={"path": ProgramIdentity.file_id(location.path), "location_id": ProgramIdentity.location_id(
        location.path, location.line, location.column, location.end_line, location.end_column)})
