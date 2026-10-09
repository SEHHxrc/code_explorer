"""实验两组完全相同的原始文件工具；不暴露 Manifest、符号索引或依赖图。"""

from __future__ import annotations

import asyncio
import os
import time

from pydantic import BaseModel, Field

from backend.app.agents.contracts import ToolResult
from backend.app.agents.policy import IGNORED_DIRECTORIES, resolve_project_path
from backend.app.agents.tools.base import AgentTool, ToolContext, ToolRegistry
from backend.app.agents.tools.source import (
    ReadFileTool,
    SearchProjectTextTool,
    MAX_SEARCH_FILES,
    MAX_SEARCH_SECONDS,
)


class ListProjectFilesArguments(BaseModel):
    """原始项目文件列表的相对目录和返回上限。"""

    path: str = Field(default=".", min_length=1, max_length=1000)
    limit: int = Field(default=100, ge=1, le=500)


class ListProjectFilesTool(AgentTool):
    """仅枚举受控目录内真实文件；不依赖静态分析产物。"""

    name = "list_project_files"
    description = "List bounded raw project file paths under a relative directory; no static analysis indexes."
    arguments_model = ListProjectFilesArguments

    async def execute(
        self, context: ToolContext, arguments: ListProjectFilesArguments
    ) -> ToolResult:
        """输入相对目录和上限，在线程中返回排序文件列表与截断标记。"""
        return await asyncio.to_thread(self._list, context, arguments)

    @staticmethod
    def _list(context: ToolContext, arguments: ListProjectFilesArguments) -> ToolResult:
        """限定文件数和遍历时间，跳过忽略目录和符号链接。"""
        target = resolve_project_path(context.project_root, arguments.path)
        if not target.is_dir():
            raise ValueError("Requested project directory does not exist")
        paths: list[str] = []
        visited = 0
        started = time.monotonic()
        for current, directories, files in os.walk(target):
            directories[:] = sorted(
                name
                for name in directories
                if name not in IGNORED_DIRECTORIES
                and not os.path.islink(os.path.join(current, name))
            )
            for name in sorted(files):
                visited += 1
                if (
                    visited > MAX_SEARCH_FILES
                    or time.monotonic() - started > MAX_SEARCH_SECONDS
                    or len(paths) >= arguments.limit
                ):
                    return ToolResult(content={"files": paths}, truncated=True)
                path = os.path.join(current, name)
                if os.path.islink(path):
                    continue
                relative = os.path.relpath(path, context.project_root).replace(
                    "\\", "/"
                )
                resolve_project_path(context.project_root, relative)
                paths.append(relative)
        return ToolResult(content={"files": paths})


def create_experiment_tool_registry() -> ToolRegistry:
    """两组使用完全相同的列文件、读文件和文本搜索权限及预算。"""
    return ToolRegistry(
        [ListProjectFilesTool(), ReadFileTool(), SearchProjectTextTool()]
    )
