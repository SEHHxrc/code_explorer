"""执行任务队列与有序审计事件的 SQLite 仓储。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.models import ExecutionEventModel, ExecutionTaskModel, SessionLocal

from .contracts import ExecutionEventView, ExecutionPlan, ExecutionTaskView, TERMINAL_EXECUTION_STATUSES


class ExecutionRepository:
    """每个方法拥有独立会话；Worker 通过条件更新原子认领任务。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self.session_factory = session_factory

    def create(self, *, task_id: str, project_id: str, user_id: str, plan: ExecutionPlan) -> ExecutionTaskView:
        """创建 queued 任务，并记录不含宿主路径的策略结果。"""
        db = self.session_factory()
        try:
            row = ExecutionTaskModel(
                id=task_id,
                project_id=project_id,
                user_id=user_id,
                kind=plan.kind,
                image=plan.image,
                argv=plan.argv,
                scan_profile=plan.scan_profile,
                status="queued",
                timeout_seconds=plan.timeout_seconds,
                cpu_limit=str(plan.cpu_limit),
                memory_mb=plan.memory_mb,
                pids_limit=plan.pids_limit,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            view = self._view(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        self.add_event(task_id, "task.queued", {
            "kind": plan.kind,
            "image": plan.image,
            "argument_count": len(plan.argv),
            "limits": {
                "timeout_seconds": plan.timeout_seconds,
                "cpu": plan.cpu_limit,
                "memory_mb": plan.memory_mb,
                "pids": plan.pids_limit,
            },
            "network": "none",
            "workspace": "read_only",
        })
        return view

    def get(self, task_id: str, user_id: str) -> ExecutionTaskView | None:
        """按任务与用户查询公开视图。"""
        db = self.session_factory()
        try:
            row = db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.id == task_id,
                ExecutionTaskModel.user_id == user_id,
            ).first()
            return self._view(row) if row else None
        finally:
            db.close()

    def count_active_for_user(self, user_id: str) -> int:
        """返回用户尚未进入终态的任务数，用于队列配额。"""
        db = self.session_factory()
        try:
            return db.query(ExecutionTaskModel.id).filter(
                ExecutionTaskModel.user_id == user_id,
                ExecutionTaskModel.status.in_(("queued", "running", "cancel_requested")),
            ).count()
        finally:
            db.close()

    def list_for_project(self, project_id: str, user_id: str, limit: int = 20) -> list[ExecutionTaskView]:
        """倒序返回项目最近的执行任务。"""
        db = self.session_factory()
        try:
            rows = db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.project_id == project_id,
                ExecutionTaskModel.user_id == user_id,
            ).order_by(ExecutionTaskModel.created_at.desc()).limit(limit).all()
            return [self._view(row) for row in rows]
        finally:
            db.close()

    def claim_next(self, worker_id: str) -> ExecutionTaskView | None:
        """使用 queued 条件更新认领最早任务；竞争失败时返回空。"""
        db = self.session_factory()
        try:
            candidate = db.query(ExecutionTaskModel.id).filter(
                ExecutionTaskModel.status == "queued",
            ).order_by(ExecutionTaskModel.created_at.asc()).first()
            if candidate is None:
                return None
            now = datetime.now(timezone.utc)
            updated = db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.id == candidate[0],
                ExecutionTaskModel.status == "queued",
            ).update({
                "status": "running",
                "worker_id": worker_id,
                "started_at": now,
                "updated_at": now,
            }, synchronize_session=False)
            db.commit()
            if updated != 1:
                return None
            row = db.query(ExecutionTaskModel).filter(ExecutionTaskModel.id == candidate[0]).first()
            view = self._view(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        self.add_event(view.id, "task.started", {"worker_id": worker_id})
        return view

    def request_cancel(self, task_id: str, user_id: str) -> ExecutionTaskView | None:
        """queued 任务直接取消；running 任务转为 cancel_requested 供 Worker 终止。"""
        db = self.session_factory()
        event_type = None
        try:
            row = db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.id == task_id,
                ExecutionTaskModel.user_id == user_id,
            ).first()
            if row is None:
                return None
            if row.status == "queued":
                row.status = "cancelled"
                row.finished_at = datetime.now(timezone.utc)
                event_type = "task.cancelled"
            elif row.status == "running":
                row.status = "cancel_requested"
                event_type = "task.cancel_requested"
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(row)
            view = self._view(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        if event_type:
            self.add_event(task_id, event_type, {})
        return view

    def heartbeat(self, task_id: str, worker_id: str) -> None:
        """更新 Worker 租约时间，不改变用户请求的取消状态。"""
        db = self.session_factory()
        try:
            db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.id == task_id,
                ExecutionTaskModel.worker_id == worker_id,
                ExecutionTaskModel.status.in_(("running", "cancel_requested")),
            ).update({"updated_at": datetime.now(timezone.utc)}, synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def recover_stale(self, older_than: datetime) -> list[str]:
        """将失去 Worker 心跳的活动任务终止为 failed，避免永久阻塞项目删除。"""
        db = self.session_factory()
        recovered = []
        try:
            rows = db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.status.in_(("running", "cancel_requested")),
                ExecutionTaskModel.updated_at < older_than,
            ).all()
            now = datetime.now(timezone.utc)
            for row in rows:
                recovered.append(row.id)
                row.status = "failed"
                row.error = "Execution worker lease expired."
                row.finished_at = now
                row.updated_at = now
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        for task_id in recovered:
            self.add_event(task_id, "task.failed", {
                "error": "Execution worker lease expired.",
                "recovered": True,
            })
        return recovered

    def is_cancel_requested(self, task_id: str) -> bool:
        """供 Worker 轮询取消标志。"""
        db = self.session_factory()
        try:
            status = db.query(ExecutionTaskModel.status).filter(
                ExecutionTaskModel.id == task_id,
            ).scalar()
            return status == "cancel_requested"
        finally:
            db.close()

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        exit_code: int | None,
        error: str | None = None,
        output_truncated: bool = False,
    ) -> None:
        """写入唯一终态并追加完成审计事件。"""
        if status not in TERMINAL_EXECUTION_STATUSES:
            raise ValueError("Invalid terminal execution status")
        db = self.session_factory()
        try:
            now = datetime.now(timezone.utc)
            db.query(ExecutionTaskModel).filter(
                ExecutionTaskModel.id == task_id,
                ExecutionTaskModel.status.in_(("running", "cancel_requested")),
            ).update({
                "status": status,
                "exit_code": exit_code,
                "error": error,
                "output_truncated": output_truncated,
                "finished_at": now,
                "updated_at": now,
            }, synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        self.add_event(task_id, f"task.{status}", {
            "exit_code": exit_code,
            "error": error,
            "output_truncated": output_truncated,
        })

    def add_event(self, task_id: str, event_type: str, payload: dict) -> ExecutionEventView:
        """追加单调递增审计事件。单 Worker 任务日志按调用顺序写入。"""
        db = self.session_factory()
        try:
            row = ExecutionEventModel(
                task_id=task_id,
                sequence=0,
                event_type=event_type,
                payload=payload,
            )
            db.add(row)
            db.flush()
            # 全局自增主键天然并发唯一，也可直接作为 SSE 断点游标。
            row.sequence = row.id
            db.commit()
            return ExecutionEventView(sequence=row.sequence, type=row.event_type, payload=row.payload)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def events_after(self, task_id: str, sequence: int) -> list[ExecutionEventView]:
        """按序号读取审计和日志事件。"""
        db = self.session_factory()
        try:
            rows = db.query(ExecutionEventModel).filter(
                ExecutionEventModel.task_id == task_id,
                ExecutionEventModel.sequence > sequence,
            ).order_by(ExecutionEventModel.sequence).all()
            return [
                ExecutionEventView(sequence=row.sequence, type=row.event_type, payload=row.payload)
                for row in rows
            ]
        finally:
            db.close()

    @staticmethod
    def _view(row: ExecutionTaskModel) -> ExecutionTaskView:
        return ExecutionTaskView(
            id=row.id,
            project_id=row.project_id,
            user_id=row.user_id,
            kind=row.kind,
            status=row.status,
            image=row.image,
            argv=list(row.argv or []),
            scan_profile=row.scan_profile,
            timeout_seconds=row.timeout_seconds,
            cpu_limit=float(row.cpu_limit),
            memory_mb=row.memory_mb,
            pids_limit=row.pids_limit,
            exit_code=row.exit_code,
            error=row.error,
            output_truncated=bool(row.output_truncated),
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
