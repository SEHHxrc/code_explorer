"""实验配对和盲评记录的数据库仓储。

TEMPORARY CONTROL GROUP / 临时对照组：baseline 字段只为实验存在，删除清单见协议文档。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.agents.contracts import AgentRunRequest

from backend.app.experiments.contracts import BlindReviewRequest
from backend.app.models import (
    ExperimentComparisonModel,
    AgentRunModel,
    AgentJobModel,
    ExperimentReviewModel,
    SessionLocal,
)


@dataclass(frozen=True)
class ComparisonRecord:
    """保存配对实验、盲态顺序和运行引用的持久化快照。"""
    comparison_id: str
    project_id: str
    user_id: str
    question: str
    # TEMPORARY CONTROL GROUP / 临时对照组：无图运行引用。
    baseline_run_id: str
    graph_run_id: str
    blind_order: dict[str, str]
    execution_order: list[str]


class ExperimentRepository:
    """封装配对实验与盲评记录的数据库访问。"""
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """使用可替换的会话工厂初始化配对实验与盲评仓储。"""
        self.session_factory = session_factory

    def create(self, record: ComparisonRecord) -> None:
        """创建并持久化新的领域记录。"""
        session = self.session_factory()
        try:
            session.add(ExperimentComparisonModel(
                id=record.comparison_id,
                project_id=record.project_id,
                user_id=record.user_id,
                question=record.question,
                baseline_run_id=record.baseline_run_id,
                graph_run_id=record.graph_run_id,
                blind_order=record.blind_order,
                execution_order=record.execution_order,
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_pair(self, record: ComparisonRecord, request: AgentRunRequest) -> None:
        """同一数据库事务创建随机排序的两个运行、队列项和配对，失败整体回滚。"""
        session = self.session_factory()
        try:
            identifiers = {"baseline": record.baseline_run_id, "security_evidence": record.graph_run_id}
            now = datetime.now(timezone.utc)
            for index, strategy in enumerate(record.execution_order):
                run_id = identifiers[strategy]
                session.add(AgentRunModel(
                    id=run_id, project_id=record.project_id, user_id=record.user_id,
                    question=request.question, use_model=True, max_steps=request.max_steps,
                    model=request.model, status="queued", created_at=now + timedelta(microseconds=index),
                ))
                session.add(AgentJobModel(run_id=run_id, strategy=strategy))
            session.add(ExperimentComparisonModel(
                id=record.comparison_id, project_id=record.project_id, user_id=record.user_id,
                question=record.question, baseline_run_id=record.baseline_run_id,
                graph_run_id=record.graph_run_id, blind_order=record.blind_order,
                execution_order=record.execution_order,
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, comparison_id: str, user_id: str) -> ComparisonRecord | None:
        """按标识和用户读取其有权访问的领域记录。"""
        session = self.session_factory()
        try:
            row = session.query(ExperimentComparisonModel).filter(
                ExperimentComparisonModel.id == comparison_id,
                ExperimentComparisonModel.user_id == user_id,
            ).first()
            if row is None:
                return None
            return ComparisonRecord(
                comparison_id=row.id,
                project_id=row.project_id,
                user_id=row.user_id,
                question=row.question,
                baseline_run_id=row.baseline_run_id,
                graph_run_id=row.graph_run_id,
                blind_order=dict(row.blind_order),
                execution_order=list(row.execution_order),
            )
        finally:
            session.close()

    def save_review(self, comparison_id: str, user_id: str, review: BlindReviewRequest) -> None:
        """新增或更新当前用户提交的盲评结果。"""
        session = self.session_factory()
        try:
            existing = session.query(ExperimentReviewModel).filter(
                ExperimentReviewModel.comparison_id == comparison_id,
                ExperimentReviewModel.user_id == user_id,
            ).first()
            values = {
                "preferred_lane": review.preferred_lane,
                "scores": {"left": review.left.model_dump(), "right": review.right.model_dump()},
                "notes": review.notes,
            }
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                session.add(ExperimentReviewModel(
                    comparison_id=comparison_id,
                    user_id=user_id,
                    **values,
                ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def has_review(self, comparison_id: str, user_id: str) -> bool:
        """返回当前用户是否已经提交盲评。"""
        session = self.session_factory()
        try:
            return session.query(ExperimentReviewModel.id).filter(
                ExperimentReviewModel.comparison_id == comparison_id,
                ExperimentReviewModel.user_id == user_id,
            ).first() is not None
        finally:
            session.close()
