"""所有 ProgramGraph 语言前端共享的模板基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.services.program_index import ProgramIdentity

from ..control_ir import ControlFunction, FrontendResult


@dataclass
class FrontendFileResult:
    """单文件解析钩子返回的函数与非致命诊断。"""

    functions: list[ControlFunction] = field(default_factory=list)
    failures: list[dict[str, object]] = field(default_factory=list)


class ProgramGraphFrontend(ABC):
    """统一文件编排、路径身份、失败隔离和覆盖率统计。"""

    language: str
    extensions: frozenset[str]

    def build(
        self,
        project_root: str | Path,
        *,
        file_paths: list[str],
    ) -> FrontendResult:
        """逐文件调用语言钩子并聚合成稳定 FrontendResult。"""
        root = Path(project_root)
        result = FrontendResult(language=self.language, files_considered=len(file_paths))
        for relative in sorted(file_paths):
            normalized = ProgramIdentity.file_id(relative)
            try:
                file_result = self._analyze_file(root, normalized)
                result.functions.extend(file_result.functions)
                result.failures.extend(file_result.failures)
                result.files_parsed += 1
            except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
                result.failures.append({
                    "path": normalized,
                    "language": self.language,
                    "reason": "parse_error",
                    "detail": type(exc).__name__,
                })
        return result

    @abstractmethod
    def _analyze_file(
        self,
        project_root: Path,
        relative_path: str,
    ) -> FrontendFileResult:
        """解析一个规范化项目相对文件，由具体语法前端实现。"""
        raise NotImplementedError
