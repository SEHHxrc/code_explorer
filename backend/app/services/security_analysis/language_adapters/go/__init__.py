"""Go 安全语法前端稳定入口；收集器和 API 识别可独立调整。"""

from pathlib import Path
from typing import Any

from backend.app.services.syntax_analysis import extensions_for_language

from ...ir import SecurityProgramIR
from ..treesitter import TreeSitterSecurityFrontend
from .collector import _GoIRCollector
from .routes import resolve_routes


class GoSecurityFrontend(TreeSitterSecurityFrontend):
    """提取 Go 函数、调用、赋值目标、参数类型和条件。"""

    language = "go"
    parser_name = "go"
    extensions = extensions_for_language(language)

    def build(self, project_root: str | Path, *, file_paths: list[str] | None = None) -> SecurityProgramIR:
        """解析文件后按包、接收者和精确匿名函数身份关联路由，不靠处理器尾名猜测。"""
        program = super().build(project_root, file_paths=file_paths)
        resolve_routes(program)
        return program

    def collect_file(
        self,
        program: SecurityProgramIR,
        path: str,
        source: bytes,
        root: Any,
    ) -> None:
        """将一个 Go 文件的可观察语法事实追加到公共 IR。"""
        collector = _GoIRCollector(path, source, root)
        collector.collect(root)
        program.functions.extend(collector.functions)
        program.calls.extend(collector.calls)
        program.conditions.extend(collector.conditions)
        program.accesses.extend(collector.accesses)


__all__ = ["GoSecurityFrontend"]
