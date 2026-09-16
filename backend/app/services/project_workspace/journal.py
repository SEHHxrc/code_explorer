"""导入操作的原子状态日志，用于异常诊断和崩溃恢复。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .contracts import WorkspaceOperation


class OperationJournal:
    """只记录资源 ID 和状态，不持久化可伪造的绝对删除路径。"""

    filename = "operation.json"

    def create(self, operation: WorkspaceOperation) -> None:
        """创建并持久化新的领域记录。"""
        self._write(operation.operation_root, {
            "schema_version": "1.0",
            "operation_id": operation.operation_id,
            "project_id": operation.project_id,
            "user_id": operation.user_id,
            "state": "created",
            "created_at": self._now(),
            "updated_at": self._now(),
        })

    def transition(self, operation: WorkspaceOperation, state: str) -> None:
        """更新当前工作区操作的持久化阶段。"""
        payload = self.read(operation.operation_root) or {}
        payload.update({
            "schema_version": "1.0",
            "operation_id": operation.operation_id,
            "project_id": operation.project_id,
            "user_id": operation.user_id,
            "state": state,
            "updated_at": self._now(),
        })
        payload.setdefault("created_at", payload["updated_at"])
        self._write(operation.operation_root, payload)

    def read(self, operation_root: Path) -> dict | None:
        """读取并反序列化工作区操作日志。"""
        path = operation_root / self.filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self, operation_root: Path, payload: dict) -> None:
        """通过临时文件替换原子写入操作日志。"""
        operation_root.mkdir(parents=True, exist_ok=True)
        target = operation_root / self.filename
        temporary = operation_root / f"{self.filename}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    @staticmethod
    def _now() -> str:
        """返回 UTC ISO 8601 时间字符串。"""
        return datetime.now(timezone.utc).isoformat()

