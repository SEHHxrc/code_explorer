"""Python 专用兼容门面；新代码应使用多语言 SecurityScanner。"""

from __future__ import annotations

from .frontends import PythonSecurityFrontend
from .registry import FrontendRegistry, RulePackRegistry, SemanticsRegistry
from .rule_engine import PythonScanResult, SecurityRuleEngine
from .rules import PYTHON_RULE_PACKS
from .scanner import SecurityScanner
from .semantics import PythonLanguageSemantics


class PythonSecurityScanner(SecurityScanner):
    """保持旧构造方式，同时委托通用多语言扫描器。"""

    def __init__(
        self,
        *,
        max_file_bytes: int = 2 * 1024 * 1024,
        frontend: PythonSecurityFrontend | None = None,
        rule_engine: SecurityRuleEngine | None = None,
    ) -> None:
        """创建只注册 Python 的兼容扫描器。"""
        python_frontend = frontend or PythonSecurityFrontend(max_file_bytes=max_file_bytes)
        super().__init__(
            frontends=FrontendRegistry([python_frontend]),
            rule_packs=RulePackRegistry(PYTHON_RULE_PACKS),
            semantics=SemanticsRegistry([PythonLanguageSemantics()]),
            rule_engine=rule_engine,
        )


__all__ = ["PythonScanResult", "PythonSecurityScanner"]
