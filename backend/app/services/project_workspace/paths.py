"""项目工作区路径生成与删除边界校验。"""

from __future__ import annotations

import os
import re
from pathlib import Path


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProjectWorkspacePaths:
    """只根据受控 ID 计算路径，不接受日志或数据库提供的删除目标。"""

    def __init__(self, root: Path | str = "backend/storage/users"):
        self.root = Path(root).resolve()

    def validate_identifier(self, value: str, label: str) -> str:
        if not _IDENTIFIER.fullmatch(value or ""):
            raise ValueError(f"Invalid {label}")
        return value

    def user_root(self, user_id: str) -> Path:
        return self.root / self.validate_identifier(user_id, "user id")

    def staging_root(self, user_id: str) -> Path:
        return self.user_root(user_id) / ".staging"

    def operation_root(self, user_id: str, operation_id: str) -> Path:
        self.validate_identifier(operation_id, "operation id")
        return self.staging_root(user_id) / operation_id

    def project_root(self, user_id: str, project_id: str) -> Path:
        self.validate_identifier(project_id, "project id")
        return self.user_root(user_id) / "projects" / project_id

    @staticmethod
    def ensure_child(path: Path | str, root: Path | str) -> Path:
        """按词法绝对路径验证目标在根下，避免跟随末端符号链接。"""
        root_path = Path(root).resolve()
        candidate = Path(os.path.abspath(path))
        if candidate == root_path or not candidate.is_relative_to(root_path):
            raise ValueError("Refusing filesystem operation outside the controlled root")
        return candidate

