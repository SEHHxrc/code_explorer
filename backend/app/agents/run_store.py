from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.agents.contracts import (
    AgentClaim,
    AgentEvent,
    AgentEvidence,
    AgentRunHistoryItem,
    AgentRunRequest,
    AgentRunSnapshot,
    AgentRunStatus,
    AgentRunView,
    AgentStrategy,
)
from backend.app.models import (
    AgentEventModel,
    AgentJobModel,
    AgentRunModel,
    SessionLocal,
)

ACTIVE_STATUSES = ("queued", "running")
_HISTORY_EVENT_FIELDS = {
    "run.started": {"project_id"},
    "context.ready": {"project_name", "characters", "evidence"},
    "model.started": {"step", "prompt_chars", "tool_count", "request_chars", "max_output_tokens"},
    "tool.requested": {"step", "call_id", "name", "arguments"},
    "tool.completed": {"step", "call_id", "name"},
    "tool.failed": {"step", "call_id", "name", "error"},
    "run.completed": {"provider", "model", "evidence"},
    "run.failed": {"error", "error_type", "retryable", "status_code", "error_code", "retry_after", "request_id"},
    "run.cancelled": set(),
}


class AgentRunStore:
    """智能体运行、持久化队列租约与有序事件仓储。"""

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        """使用可替换的 SQLAlchemy 会话工厂初始化运行、队列与事件仓储。"""
        self.session_factory = session_factory or SessionLocal

    def create(
        self,
        *,
        run_id: str,
        project_id: str,
        user_id: str,
        request: AgentRunRequest,
        strategy: AgentStrategy = "default",
    ) -> AgentRunView:
        """在一个事务中创建公开运行记录和待认领队列项。"""
        if strategy not in {"default", "graph", "baseline", "security_evidence"}:
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
                model=request.model,
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

    def list_project_history(
        self,
        project_id: str,
        user_id: str,
        *,
        limit: int = 30,
    ) -> list[AgentRunHistoryItem]:
        """按时间倒序返回普通 Agent 历史；实验 graph/baseline 运行保持隔离。"""
        db = self.session_factory()
        try:
            rows = db.query(AgentRunModel, AgentJobModel).outerjoin(
                AgentJobModel, AgentJobModel.run_id == AgentRunModel.id,
            ).filter(
                AgentRunModel.project_id == project_id,
                AgentRunModel.user_id == user_id,
                or_(AgentJobModel.run_id.is_(None), AgentJobModel.strategy == "default"),
            ).order_by(AgentRunModel.created_at.desc()).limit(max(1, min(limit, 100))).all()
            metrics = self._history_metrics(
                db,
                [run.id for run, _ in rows],
            )
            return [
                AgentRunHistoryItem(
                    id=run.id,
                    status=run.status,
                    question_preview=self._question_preview(run.question),
                    provider=run.provider,
                    model=run.model,
                    strategy=job.strategy if job else "default",
                    tool_calls=metrics.get(run.id, {}).get("tool_calls", 0),
                    evidence_count=len(metrics.get(run.id, {}).get("evidence", [])),
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
                for run, job in rows
            ]
        finally:
            db.close()

    def snapshot(self, run_id: str, user_id: str) -> AgentRunSnapshot | None:
        """返回一次运行的可回放快照，并剥离前端不展示的工具结果正文。"""
        db = self.session_factory()
        try:
            pair = db.query(AgentRunModel, AgentJobModel).outerjoin(
                AgentJobModel, AgentJobModel.run_id == AgentRunModel.id,
            ).filter(
                AgentRunModel.id == run_id,
                AgentRunModel.user_id == user_id,
            ).first()
            if pair is None:
                return None
            run, job = pair
            rows = db.query(AgentEventModel).filter(
                AgentEventModel.run_id == run_id,
            ).order_by(AgentEventModel.sequence).all()
            events = [event for row in rows if (event := self._history_event(row)) is not None]
            evidence = self._collect_evidence(rows)
            return AgentRunSnapshot(
                run=self._view(run),
                strategy=job.strategy if job else "default",
                events=events,
                evidence=evidence,
            )
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
            if row is None or job is None:
                return None
            return AgentClaim(
                run_id=row.id,
                project_id=row.project_id,
                user_id=row.user_id,
                question=row.question,
                use_model=bool(row.use_model),
                max_steps=row.max_steps,
                model=row.model,
                strategy=self._strategy(job.strategy),
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

    def update(self, run_id: str, **values: Any) -> None:
        """更新指定运行字段和更新时间。"""
        allowed_fields = {"status", "provider", "model", "answer", "error"}
        unsupported = set(values) - allowed_fields
        if unsupported:
            raise ValueError(f"Unsupported agent run fields: {sorted(unsupported)}")
        db = self.session_factory()
        try:
            row = db.query(AgentRunModel).filter(AgentRunModel.id == run_id).first()
            if row is None:
                return
            for name, value in values.items():
                setattr(row, name, value)
            row.updated_at = datetime.now(timezone.utc)
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
            db.refresh(row)
            return AgentEvent(
                sequence=row.sequence,
                type=row.event_type,
                payload=row.payload,
                created_at=row.created_at,
            )
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
                AgentEvent(
                    sequence=row.sequence,
                    type=row.event_type,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ]
        finally:
            db.close()

    @classmethod
    def _history_metrics(cls, db: Session, run_ids: list[str]) -> dict[str, dict[str, Any]]:
        """一次查询计算历史列表所需的工具调用数和去重证据。"""
        metrics = {run_id: {"tool_calls": 0, "rows": []} for run_id in run_ids}
        if not run_ids:
            return metrics
        rows = db.query(AgentEventModel).filter(
            AgentEventModel.run_id.in_(run_ids),
            AgentEventModel.event_type.in_((
                "context.ready", "tool.requested", "tool.completed", "run.completed",
            )),
        ).order_by(AgentEventModel.sequence).all()
        for row in rows:
            item = metrics[row.run_id]
            if row.event_type == "tool.requested":
                item["tool_calls"] += 1
            item["rows"].append(row)
        for item in metrics.values():
            item["evidence"] = cls._collect_evidence(item.pop("rows"))
        return metrics

    @staticmethod
    def _question_preview(question: str, limit: int = 120) -> str:
        """把问题压缩为单行历史标签并限制列表响应体积。"""
        compact = " ".join((question or "").split())
        return compact if len(compact) <= limit else compact[: limit - 1] + "…"

    @staticmethod
    def _collect_evidence(rows: list[AgentEventModel]) -> list[AgentEvidence]:
        """从上下文、工具完成和最终事件中恢复并去重结构化证据。"""
        evidence: list[AgentEvidence] = []
        seen: set[tuple[str, int | None, str | None]] = set()
        for row in rows:
            payload = row.payload or {}
            candidates = list(payload.get("evidence") or [])
            result = payload.get("result")
            if isinstance(result, dict):
                candidates.extend(result.get("evidence") or [])
            for candidate in candidates:
                try:
                    item = AgentEvidence.model_validate(candidate)
                except (TypeError, ValueError):
                    continue
                key = (item.path, item.line, item.symbol)
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
        return evidence[:80]

    @staticmethod
    def _history_event(row: AgentEventModel) -> AgentEvent | None:
        """把持久化事件裁剪为前端回放需要的安全字段。"""
        allowed = _HISTORY_EVENT_FIELDS.get(row.event_type)
        if allowed is None:
            return None
        payload = row.payload or {}
        return AgentEvent(
            sequence=row.sequence,
            type=row.event_type,
            payload={key: payload[key] for key in allowed if key in payload},
            created_at=row.created_at,
        )

    @staticmethod
    def _view(row: AgentRunModel) -> AgentRunView:
        """将数据库记录转换为脱离会话的公开视图。"""
        return AgentRunView(
            id=row.id,
            project_id=row.project_id,
            question=row.question,
            status=AgentRunStore._status(row.status),
            provider=row.provider,
            model=row.model,
            answer=row.answer,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _strategy(value: str) -> AgentStrategy:
        """把数据库策略值收敛为公开契约，未知旧值退回默认策略。"""
        if value == "graph":
            return "graph"
        if value == "security_evidence":
            return "security_evidence"
        if value == "baseline":
            return "baseline"
        return "default"

    @staticmethod
    def _status(value: str) -> AgentRunStatus:
        """校验数据库运行状态；未知值作为失败状态安全降级。"""
        if value == "queued":
            return "queued"
        if value == "running":
            return "running"
        if value == "completed":
            return "completed"
        if value == "cancelled":
            return "cancelled"
        return "failed"
