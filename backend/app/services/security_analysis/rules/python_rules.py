"""兼容导出；新代码应按规则包注册 Python 核心和 FastAPI 规则。"""

from __future__ import annotations

from .python import PYTHON_RULE_PACKS, PYTHON_STANDARD_LIBRARY_PACK

PYTHON_CALL_RULES = PYTHON_STANDARD_LIBRARY_PACK.call_rules

__all__ = ["PYTHON_CALL_RULES", "PYTHON_RULE_PACKS"]
