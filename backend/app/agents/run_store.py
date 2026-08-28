# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.agents.contracts import AgentClaim, AgentEvent, AgentRunRequest, AgentRunView
from backend.app.models import AgentEventModel, AgentJobModel, AgentRunModel, SessionLocal


ACTIVE_STATUSES = ("queued", "running")


class AgentRunStore:
    """智能体运行、持久化队列租约与有序事件仓储。"""

    def __init__(self, session_factory: Callable[[], Session] | None = None):
        self.session_factory = session_factory or SessionLocal

    def create(
        self,
        *,
        run_id: str,
        project_id: str,
        user_id: str,
        request: AgentRunRequest,
        strategy: str = "default",
    ) -> AgentRunView:
        """在一个事务中创建公开运行记录和待认领队列项。"""
        if strategy not in {"default", "graph", "baseline"}:
            raise ValueError("Unsupported agent strategy")
        db = self.session_factory()
        try:
            row = AgentRunModel(
                id=run_id,
                project_id=project_id,
                user_id=user_id,
                question=request.question,
                use_model=request.use_model,
                max_steps=request.max_steps,
                status="queued",
            )
            db.add(row)
            db.add(AgentJobModel(run_id=run_id, strategy=strategy))
            db.commit()
            db.refresh(row)
            return self._view(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def ensure_jobs(self) -> int:
        """为升级前遗留的活动运行补建默认队列项，返回补建数量。"""
        db = self.session_factory()
        created = 0
        try:
            rows = db.query(AgentRunModel).outerjoin(
                AgentJobModel, AgentJobModel.run_id == AgentRunModel.id,
            ).filter(
                AgentRunModel.status.in_(ACTIVE_STATUSES),
                AgentJobModel.run_id.is_(None),
            ).all()
            for row in rows:
                db.add(AgentJobModel(run_id=row.id, strategy="default"))
                created += 1
            db.commit()
            return created
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, run_id: str, user_id: str) -> AgentRunView | None:
        """按运行和用户 ID 查询公开运行视图。"""
        db = self.session_factory()
        try:
            row = db.query(AgentRunModel).filter(
                AgentRunModel.id == run_id,
                AgentRunModel.user_id == user_id,
            ).first()
            return self._view(row) if row else None
        finally:
            db.close()

    def get_row_data(self, run_id: str, user_id: str) -> dict | None:
        """输出兼容旧调用方的最小运行字段。"""
        db = self.session_factory()
        try:
            row = db.query(AgentRunModel).filter(
                AgentRunModel.id == run_id,
                AgentRunModel.user_id == user_id,
            ).first()
            if not row:
                return None
            return {
                "id": row.id,
                "project_id": row.project_id,
                "user_id": row.user_id,
                "question": row.question,
                "use_model": row.use_model,
                "max_steps": row.max_steps,
                "status": row.status,
            }
        finally:
            db.close()

    def claim_next(self, worker_id: str) -> AgentClaim | None:
        """以 queued 条件更新原子认领最早运行；竞争失败返回空。"""
        db = self.session_factory()
        try:
            candidate = db.query(AgentRunModel.id).join(
                AgentJobModel, AgentJobModel.run_id == AgentRunModel.id,
            ).filter(
                AgentRunModel.status == "queued",
                AgentJobModel.cancel_requested.is_(False),
            ).order_by(AgentRunModel.created_at.asc()).first()
            if candidate is None:
                return None
            now = datetime.now(timezone.utc)
            updated = db.query(AgentRunModel).filter(
                AgentRunModel.id == candidate[0],
                AgentRunModel.status == "queued",
            ).update({
                "status": "running",
                "updated_at": now,
            }, synchronize_session=False)
            if updated != 1:
                db.rollback()
                return None
            db.query(AgentJobModel).filter(
                AgentJobModel.run_id == candidate[0],
            ).update({
                "worker_id": worker_id,
                "attempts": AgentJobModel.attempts + 1,
                "updated_at": now,
            }, synchronize_session=False)
            db.commit()
            row = db.query(AgentRunModel).filter(AgentRunModel.id == candidate[0]).first()
            job = db.query(AgentJobModel).filter(AgentJobModel.run_id == candidate[0]).first()
            return AgentClaim(
                run_id=row.id,
                project_id=row.project_id,
                user_id=row.user_id,
                question=row.question,
                use_model=bool(row.use_model),
                max_steps=row.max_steps,
                strategy=job.strategy,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def request_cancel(self, run_id: str, user_id: str) -> AgentRunView | None:
        """排队任务直接取消；运行任务写入持久化取消标志。"""
        db = self.session_factory()
        event_type = None
        try:
            row = db.query(AgentRunModel).filter(
                AgentRunModel.id == run_id,
                AgentRunModel.user_id == user_id,
            ).first()
            if row is None:
                return None
            job = db.query(AgentJobModel).filter(AgentJobModel.run_id == run_id).first()
            if job is None and row.status in ACTIVE_STATUSES:
                job = AgentJobModel(run_id=run_id, strategy="default")
                db.add(job)
            if row.status == "queued":
                row.status = "cancelled"
                row.updated_at = datetime.now(timezone.utc)
                if job:
                    job.cancel_requested = True
                    job.updated_at = row.updated_at
                event_type = "run.cancelled"
            elif row.status == "running":
                if job:
                    job.cancel_requested = True
                    job.updated_at = datetime.now(timezone.utc)
                    event_type = "run.cancel_requested"
            db.commit()
            db.refresh(row)
            view = self._view(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        if event_type:
            self.add_event(run_id, event_type, {})
        return view

    def heartbeat(self, run_id: str, worker_id: str) -> None:
        """续租当前 Worker 所认领的运行。"""
        db = self.session_factory()
        try:
            db.query(AgentJobModel).filter(
                AgentJobModel.run_id == run_id,
                AgentJobModel.worker_id == worker_id,
            ).update({"updated_at": datetime.now(timezone.utc)}, synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def is_cancel_requested(self, run_id: str) -> bool:
        """返回运行是否收到持久化取消请求。"""
        db = self.session_factory()
        try:
            return bool(db.query(AgentJobModel.cancel_requested).filter(
                AgentJobModel.run_id == run_id,
            ).scalar())
        finally:
            db.close()

    def recover_stale(self, older_than: datetime) -> list[str]:
        """终结租约过期的运行；不自动重试，避免重复产生模型费用。"""
        db = self.session_factory()
        recovered: list[str] = []
        try:
            rows = db.query(AgentRunModel, AgentJobModel).join(
                AgentJobModel, AgentJobModel.run_id == AgentRunModel.id,
            ).filter(
                AgentRunModel.status == "running",
                AgentJobModel.updated_at < older_than,
            ).all()
            now = datetime.now(timezone.utc)
            for run, job in rows:
                recovered.append(run.id)
                run.status = "failed"
                run.error = "Agent worker lease expired."
                run.updated_at = now
                job.worker_id = None
                job.updated_at = now
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        for run_id in recovered:
            self.add_event(run_id, "run.failed", {
                "error": "Agent worker lease expired.",
                "recovered": True,
            })
        return recovered

    def finish_job(self, run_id: str, worker_id: str) -> None:
        """释放 Worker 租约；运行终态由编排器写入。"""
        db = self.session_factory()
        try:
            db.query(AgentJobModel).filter(
                AgentJobModel.run_id == run_id,
                AgentJobModel.worker_id == worker_id,
            ).update({
                "worker_id": None,
                "updated_at": datetime.now(timezone.utc),
            }, synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def fail_claim(self, run_id: str, public_error: str) -> None:
        """在 Worker 无法准备运行上下文时安全终结已认领任务。"""
        self.update(run_id, status="failed", error=public_error)
        self.add_event(run_id, "run.failed", {"error": public_error})

    def update(self, run_id: str, **values) -> None:
        """更新指定运行字段和更新时间。"""
        db = self.session_factory()
        try:
            values["updated_at"] = datetime.now(timezone.utc)
            db.query(AgentRunModel).filter(AgentRunModel.id == run_id).update(values)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_event(self, run_id: str, event_type: str, payload: dict) -> AgentEvent:
        """通过全局自增主键生成并发安全、单调递增的 SSE 游标。"""
        db = self.session_factory()
        try:
            row = AgentEventModel(
                run_id=run_id,
                sequence=0,
                event_type=event_type,
                payload=payload,
            )
            db.add(row)
            db.flush()
            row.sequence = row.id
            db.commit()
            return AgentEvent(sequence=row.sequence, type=row.event_type, payload=row.payload)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def events_after(self, run_id: str, sequence: int) -> list[AgentEvent]:
        """按全局单调序号读取指定运行的后续事件。"""
        db = self.session_factory()
        try:
            rows = db.query(AgentEventModel).filter(
                AgentEventModel.run_id == run_id,
                AgentEventModel.sequence > sequence,
            ).order_by(AgentEventModel.sequence).all()
            return [
                AgentEvent(sequence=row.sequence, type=row.event_type, payload=row.payload)
                for row in rows
            ]
        finally:
            db.close()

    @staticmethod
    def _view(row: AgentRunModel) -> AgentRunView:
        return AgentRunView(
            id=row.id,
            project_id=row.project_id,
            question=row.question,
            status=row.status,
            provider=row.provider,
            model=row.model,
            answer=row.answer,
            error=row.error,
        )
