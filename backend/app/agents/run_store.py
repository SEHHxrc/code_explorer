# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func

from backend.app.agents.contracts import AgentEvent, AgentRunRequest, AgentRunView
from backend.app.models import AgentEventModel, AgentRunModel, SessionLocal


class AgentRunStore:
    """智能体运行与有序事件的 SQLite 存储门面。

    每个公开方法自行创建并关闭数据库会话；输入为运行标识和领域对象，输出为与
    ORM 解耦的 Pydantic 视图。
    """

    def create(self, *, run_id: str, project_id: str, user_id: str, request: AgentRunRequest) -> AgentRunView:
        """创建排队中的运行记录并输出其公开视图。"""
        db = SessionLocal()
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
            db.commit()
            return self._view(row)
        finally:
            db.close()

    def get(self, run_id: str, user_id: str) -> AgentRunView | None:
        """按运行和用户 ID 查询；找到时输出运行视图，否则输出 ``None``。"""
        db = SessionLocal()
        try:
            row = db.query(AgentRunModel).filter(
                AgentRunModel.id == run_id,
                AgentRunModel.user_id == user_id,
            ).first()
            return self._view(row) if row else None
        finally:
            db.close()

    def get_row_data(self, run_id: str, user_id: str) -> dict | None:
        """输出编排器恢复运行所需的最小字段字典；记录不存在时输出 ``None``。"""
        db = SessionLocal()
        try:
            row = db.query(AgentRunModel).filter(
                AgentRunModel.id == run_id,
                AgentRunModel.user_id == user_id,
            ).first()
            if not row:
                return None
            return {
                "id": row.id, "project_id": row.project_id, "user_id": row.user_id,
                "question": row.question, "use_model": row.use_model, "max_steps": row.max_steps,
                "status": row.status,
            }
        finally:
            db.close()

    def update(self, run_id: str, **values) -> None:
        """更新指定运行字段和更新时间；输入字段键值，无返回值。"""
        db = SessionLocal()
        try:
            values["updated_at"] = datetime.now(timezone.utc)
            db.query(AgentRunModel).filter(AgentRunModel.id == run_id).update(values)
            db.commit()
        finally:
            db.close()

    def add_event(self, run_id: str, event_type: str, payload: dict) -> AgentEvent:
        """追加下一序号事件并输出保存后的领域事件。"""
        db = SessionLocal()
        try:
            last = db.query(func.max(AgentEventModel.sequence)).filter(
                AgentEventModel.run_id == run_id,
            ).scalar() or 0
            row = AgentEventModel(
                run_id=run_id,
                sequence=last + 1,
                event_type=event_type,
                payload=payload,
            )
            db.add(row)
            db.commit()
            return AgentEvent(sequence=row.sequence, type=row.event_type, payload=row.payload)
        finally:
            db.close()

    def events_after(self, run_id: str, sequence: int) -> list[AgentEvent]:
        """输入运行 ID 和已消费序号，按升序输出其后的全部事件。"""
        db = SessionLocal()
        try:
            rows = db.query(AgentEventModel).filter(
                AgentEventModel.run_id == run_id,
                AgentEventModel.sequence > sequence,
            ).order_by(AgentEventModel.sequence).all()
            return [AgentEvent(sequence=row.sequence, type=row.event_type, payload=row.payload) for row in rows]
        finally:
            db.close()

    @staticmethod
    def _view(row: AgentRunModel) -> AgentRunView:
        """将 ORM 行转换为不暴露内部字段的运行视图。"""
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
