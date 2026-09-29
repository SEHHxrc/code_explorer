"""多语言安全前端抽象。"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import IGNORED_SOURCE_DIRECTORIES

from ..ir import SecurityProgramIR


class LanguageFrontend(ABC):
    """把一种语言的项目文件转换成通用 SecurityProgramIR。"""

    language: str
    extensions: frozenset[str]

    @abstractmethod
    def build(
        self,
        project_root: str | Path,
        *,
        file_paths: list[str] | None = None,
    ) -> SecurityProgramIR:
        """解析指定文件并返回规则无关 IR。"""
        raise NotImplementedError

    def normalized_paths(
        self,
        project_root: Path,
        file_paths: list[str] | None,
    ) -> list[str]:
        """规范化显式分析范围，或在缺省时执行受控源码发现。"""
        if file_paths is not None:
            return sorted({
                ProgramIdentity.file_id(path)
                for path in file_paths
                if Path(path).suffix.lower() in self.extensions
            })
        discovered: list[str] = []
        for current, directories, filenames in os.walk(project_root):
            directories[:] = sorted(
                name for name in directories
                if name not in IGNORED_SOURCE_DIRECTORIES and not name.startswith(".")
            )
            discovered.extend(
                (Path(current) / filename).relative_to(project_root).as_posix()
                for filename in sorted(filenames)
                if Path(filename).suffix.lower() in self.extensions
            )
        return discovered
