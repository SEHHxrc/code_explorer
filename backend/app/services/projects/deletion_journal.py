"""跨文件系统与数据库项目删除操作的持久化恢复日志。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ProjectDeletionOperation:
    """一次删除事务恢复所需的受控身份和阶段。"""

    operation_id: str
    project_id: str
    user_id: str
    state: str


class ProjectDeletionJournal:
    """使用原子 JSON 文件保存待恢复的项目删除操作。"""

    def __init__(self, root: Path | str = "backend/storage/deletion_journal") -> None:
        """输入受控日志根目录并保存绝对路径。"""
        self.root = Path(root).resolve()

    def begin(self, operation_id: str, project_id: str, user_id: str) -> ProjectDeletionOperation:
        """创建 prepared 状态的删除操作日志。"""
        operation = ProjectDeletionOperation(
            operation_id=self._identifier(operation_id, "operation id"),
            project_id=self._identifier(project_id, "project id"),
            user_id=self._identifier(user_id, "user id"),
            state="prepared",
        )
        self._write(operation)
        return operation

    def transition(
        self,
        operation: ProjectDeletionOperation,
        state: str,
    ) -> ProjectDeletionOperation:
        """持久化删除操作的新阶段并返回新快照。"""
        updated = ProjectDeletionOperation(
            operation_id=operation.operation_id,
            project_id=operation.project_id,
            user_id=operation.user_id,
            state=state,
        )
        self._write(updated)
        return updated

    def list_pending(self) -> list[ProjectDeletionOperation]:
        """读取所有格式有效的待恢复删除操作。"""
        if not self.root.is_dir():
            return []
        operations: list[ProjectDeletionOperation] = []
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                operation = ProjectDeletionOperation(
                    operation_id=self._identifier(str(payload["operation_id"]), "operation id"),
                    project_id=self._identifier(str(payload["project_id"]), "project id"),
                    user_id=self._identifier(str(payload["user_id"]), "user id"),
                    state=str(payload["state"]),
                )
                if path == self._path(operation.operation_id):
                    operations.append(operation)
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return operations

    def remove(self, operation_id: str) -> None:
        """幂等删除指定操作日志及可能残留的临时文件。"""
        for path in (self._path(operation_id), self._path(operation_id, ".tmp")):
            path.unlink(missing_ok=True)
        try:
            self.root.rmdir()
        except OSError:
            pass

    def _write(self, operation: ProjectDeletionOperation) -> None:
        """通过临时文件替换原子保存操作快照。"""
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self._path(operation.operation_id, ".tmp")
        temporary.write_text(json.dumps(asdict(operation), ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._path(operation.operation_id))

    def _path(self, operation_id: str, suffix: str = ".json") -> Path:
        """返回受控日志文件路径。"""
        return self.root / f"{self._identifier(operation_id, 'operation id')}{suffix}"

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        """校验日志身份字段可安全参与路径计算。"""
        if not _IDENTIFIER.fullmatch(value or ""):
            raise ValueError(f"Invalid {label}")
        return value
