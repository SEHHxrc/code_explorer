"""受控工作区的唯一文件系统变更入口。"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any

from .paths import ProjectWorkspacePaths


class WorkspaceFilesystem:
    """创建、发布和幂等删除工作区，并处理 Windows 只读文件。"""

    def __init__(self, paths: ProjectWorkspacePaths) -> None:
        """输入受控路径服务，初始化唯一的工作区文件变更边界。"""
        self.paths = paths

    def create_operation(self, user_id: str, operation_id: str) -> tuple[Path, Path]:
        """创建用户隔离的暂存操作目录。"""
        operation_root = self.paths.operation_root(user_id, operation_id)
        self.paths.ensure_child(operation_root, self.paths.staging_root(user_id))
        operation_root.mkdir(parents=True, exist_ok=False)
        source_root = operation_root / "workspace"
        source_root.mkdir()
        return operation_root, source_root

    def publish(self, user_id: str, project_id: str, source_root: Path) -> Path:
        """把已清洗的暂存工作区原子发布为项目目录。"""
        operation_root = source_root.parent
        self.paths.ensure_child(operation_root, self.paths.staging_root(user_id))
        final_root = self.paths.project_root(user_id, project_id)
        self.paths.ensure_child(final_root, self.paths.user_root(user_id) / "projects")
        final_root.parent.mkdir(parents=True, exist_ok=True)
        if final_root.exists() or final_root.is_symlink():
            raise FileExistsError("Project workspace already exists")
        source_root.replace(final_root)
        return final_root

    def remove_operation(self, user_id: str, operation_id: str) -> None:
        """删除受控的暂存操作目录。"""
        target = self.paths.operation_root(user_id, operation_id)
        self._remove(target, self.paths.staging_root(user_id))

    def remove_project(self, user_id: str, project_id: str) -> None:
        """删除受控的项目工作目录。"""
        target = self.paths.project_root(user_id, project_id)
        self._remove(target, self.paths.user_root(user_id) / "projects")

    def quarantine_project(
        self,
        user_id: str,
        project_id: str,
        operation_id: str,
    ) -> bool:
        """把项目工作区原子移动到删除隔离区，并返回工作区是否存在。"""
        source = self.paths.project_root(user_id, project_id)
        self.paths.ensure_child(source, self.paths.user_root(user_id) / "projects")
        if not source.exists() and not source.is_symlink():
            return False
        operation_root = self.paths.deletion_operation_root(user_id, operation_id)
        self.paths.ensure_child(operation_root, self.paths.deletion_root(user_id))
        operation_root.mkdir(parents=True, exist_ok=True)
        target = operation_root / "workspace"
        if target.exists() or target.is_symlink():
            raise FileExistsError("Project deletion quarantine already exists")
        source.replace(target)
        return True

    def restore_quarantined_project(
        self,
        user_id: str,
        project_id: str,
        operation_id: str,
    ) -> None:
        """把删除隔离区中的项目工作区恢复到正式位置。"""
        operation_root = self.paths.deletion_operation_root(user_id, operation_id)
        source = operation_root / "workspace"
        if not source.exists() and not source.is_symlink():
            return
        target = self.paths.project_root(user_id, project_id)
        if target.exists() or target.is_symlink():
            raise FileExistsError("Refusing to overwrite an existing project workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        try:
            operation_root.rmdir()
        except OSError:
            pass

    def purge_project_quarantine(self, user_id: str, operation_id: str) -> None:
        """幂等清除一次已经提交的项目删除隔离目录。"""
        target = self.paths.deletion_operation_root(user_id, operation_id)
        self._remove(target, self.paths.deletion_root(user_id))

    def remove_child(self, target: Path, root: Path) -> None:
        """删除受控根目录下的指定子路径。"""
        self._remove(target, root)

    def _remove(self, target: Path, root: Path) -> None:
        """校验路径边界后递归删除目标。"""
        controlled = self.paths.ensure_child(target, root)
        if controlled.is_symlink():
            controlled.unlink(missing_ok=True)
            return
        if not controlled.exists():
            return
        if controlled.is_dir():
            shutil.rmtree(controlled, onerror=self._remove_readonly)
        else:
            controlled.unlink()

    @staticmethod
    def _remove_readonly(
        function: Callable[[str], Any],
        path: str,
        _exc_info: tuple[type[BaseException], BaseException, TracebackType],
    ) -> None:
        """清除只读标志并重试文件删除。"""
        os.chmod(path, stat.S_IWRITE)
        function(path)

