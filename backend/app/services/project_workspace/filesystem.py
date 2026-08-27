"""受控工作区的唯一文件系统变更入口。"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from .paths import ProjectWorkspacePaths


class WorkspaceFilesystem:
    """创建、发布和幂等删除工作区，并处理 Windows 只读文件。"""

    def __init__(self, paths: ProjectWorkspacePaths):
        self.paths = paths

    def create_operation(self, user_id: str, operation_id: str) -> tuple[Path, Path]:
        operation_root = self.paths.operation_root(user_id, operation_id)
        self.paths.ensure_child(operation_root, self.paths.staging_root(user_id))
        operation_root.mkdir(parents=True, exist_ok=False)
        source_root = operation_root / "workspace"
        source_root.mkdir()
        return operation_root, source_root

    def publish(self, user_id: str, project_id: str, source_root: Path) -> Path:
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
        target = self.paths.operation_root(user_id, operation_id)
        self._remove(target, self.paths.staging_root(user_id))

    def remove_project(self, user_id: str, project_id: str) -> None:
        target = self.paths.project_root(user_id, project_id)
        self._remove(target, self.paths.user_root(user_id) / "projects")

    def remove_child(self, target: Path, root: Path) -> None:
        self._remove(target, root)

    def _remove(self, target: Path, root: Path) -> None:
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
    def _remove_readonly(function, path: str, _exc_info) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)

