"""Tree-sitter 安全前端共享的文件编排和失败隔离模板。"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any

from backend.app.services.syntax_analysis import (
    DEFAULT_TREE_SITTER_PARSER_POOL,
    TreeSitterParserPool,
)

from ..ir import SecurityProgramIR
from backend.app.services.syntax_analysis.javascript import parser_for_file
from backend.app.services.syntax_analysis.source_units import SourceUnit, source_unit
from .base import LanguageFrontend


class TreeSitterSecurityFrontend(LanguageFrontend):
    """统一复用路径发现、体量限制、解析器池和恢复性诊断。"""

    parser_name: str

    def __init__(
        self,
        *,
        max_file_bytes: int = 2 * 1024 * 1024,
        parser_pool: TreeSitterParserPool | None = None,
    ) -> None:
        """设置单文件上限并允许注入线程安全的共享解析器池。"""
        self.max_file_bytes = max_file_bytes
        self.parser_pool = parser_pool or DEFAULT_TREE_SITTER_PARSER_POOL

    def build(
        self,
        project_root: str | Path,
        *,
        file_paths: list[str] | None = None,
    ) -> SecurityProgramIR:
        """逐文件构建 IR；单文件失败不会中断其他文件。"""
        root = Path(project_root).resolve()
        paths = self.normalized_paths(root, file_paths)
        program = SecurityProgramIR(language=self.language, files_considered=len(paths))
        for relative in paths:
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
                if target.stat().st_size > self.max_file_bytes:
                    program.failures.append({"path": relative, "reason": "file_too_large"})
                    continue
                source = target.read_bytes()
                unit = source_unit(relative, source, self.language)
                if unit.language != self.language:
                    program.files_considered -= 1
                    continue
                source = unit.source
                tree = self.parser_pool.get(parser_for_file(self.parser_name, relative)).parse(source)
                if tree.root_node.has_error:
                    program.failures.append({
                        "path": relative,
                        "language": self.language,
                        "reason": f"{self.language}_partial_parse_error",
                    })
                self.collect_file_context(program, relative, unit, tree.root_node, root)
                program.failures.extend({"path": relative, "language": self.language, "reason": reason} for reason in unit.diagnostics)
                program.files_scanned += 1
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                program.failures.append({
                    "path": relative,
                    "language": self.language,
                    "reason": f"{self.language}_parse_error",
                    "detail": type(exc).__name__,
                })
        return program

    def collect_file_context(
        self, program: SecurityProgramIR, path: str, unit: SourceUnit,
        root: Any, project_root: Path,
    ) -> None:
        """提供受控项目上下文；默认适配器不读取额外配置或保存跨项目状态。"""
        self.collect_unit(program, path, unit, root)

    def collect_unit(self, program: SecurityProgramIR, path: str, unit: SourceUnit, root: Any) -> None:
        """默认只消费脚本视图，嵌入语言前端可附加模板事实。"""
        self.collect_file(program, path, unit.source, root)

    @abstractmethod
    def collect_file(
        self,
        program: SecurityProgramIR,
        path: str,
        source: bytes,
        root: Any,
    ) -> None:
        """把一个已解析文件追加到项目级安全 IR。"""
        raise NotImplementedError
