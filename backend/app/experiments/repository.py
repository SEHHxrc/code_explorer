"""实验配对和盲评记录的数据库仓储。

TEMPORARY CONTROL GROUP / 临时对照组：baseline 字段只为实验存在，删除清单见协议文档。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session, aliased

from backend.app.agents.contracts import AgentRunRequest

from backend.app.experiments.contracts import BlindReviewRequest
from backend.app.models import (
    ExperimentComparisonModel,
    AgentRunModel,
    AgentJobModel,
    ExperimentReviewModel,
    SessionLocal,
    ExperimentAuditSessionModel,
    ExperimentAuditRecordModel,
)
from .audit.repository import AuditRepository, lock_writes
from .audit.policy import contract_digest
from backend.app.agents.reporting import execution_policy
from backend.app.llm.settings import get_final_report_mode


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
    created_at: datetime | None = None


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

    def create_pair(self, record: ComparisonRecord, request: AgentRunRequest, *, full_source_inputs: dict[str, dict[str, Any]] | None = None, cumulative_inputs: dict[str, dict[str, Any]] | None = None, claimed_by: str | None = None) -> None:
        """同一数据库事务创建随机排序的两个运行、队列项和配对，失败整体回滚。"""
        session = self.session_factory()
        try:
            from .context import SECURITY_EXPERIMENT_INSTRUCTIONS
            from .report import REPORT_VERSION
            from .tools import create_experiment_tool_registry
            from .full_source import FULL_SOURCE_INSTRUCTIONS
            from .cumulative import INSTRUCTIONS, policy, tool_registry
            if full_source_inputs is not None and cumulative_inputs is not None:
                raise ValueError("一个配对只能使用一个冻结输入协议。")
            single_shot = full_source_inputs is not None
            frozen_inputs = full_source_inputs if single_shot else cumulative_inputs
            lock_writes(session)
            identifiers = {"baseline": record.baseline_run_id, "security_evidence": record.graph_run_id}
            contract = {run_id: contract_digest(FULL_SOURCE_INSTRUCTIONS if single_shot else SECURITY_EXPERIMENT_INSTRUCTIONS,
                        [] if single_shot else create_experiment_tool_registry(with_evidence=strategy == "security_evidence").schemas(), REPORT_VERSION,
                        execution_policy(request.max_steps, get_final_report_mode(), batch_mode=not single_shot, full_source=single_shot, model=request.model))
                        for strategy, run_id in identifiers.items()}
            if cumulative_inputs is not None:
                contract = {run_id: contract_digest(INSTRUCTIONS,
                    tool_registry(cumulative_inputs[strategy]["_cumulative_input"]).schemas(), REPORT_VERSION,
                    policy(request.max_steps, get_final_report_mode(), cumulative_inputs[strategy]["_cumulative_input"]["budget_bytes"],
                           cumulative_inputs[strategy]["_cumulative_input"].get("selection_version"), model=request.model))
                    for strategy, run_id in identifiers.items()}
            AuditRepository(self.session_factory).reserve_pair(session, record.comparison_id, record.project_id,
                record.user_id, list(identifiers.values()), contract,
                input_snapshots={run_id: frozen_inputs[strategy] for strategy, run_id in identifiers.items()} if frozen_inputs is not None else None)
            now = datetime.now(timezone.utc)
            for index, strategy in enumerate(record.execution_order):
                run_id = identifiers[strategy]
                session.add(AgentRunModel(
                    id=run_id, project_id=record.project_id, user_id=record.user_id,
                    question=request.question, use_model=True, max_steps=request.max_steps,
                    model=request.model, status="running" if claimed_by else "queued", created_at=now + timedelta(microseconds=index),
                ))
                session.add(AgentJobModel(run_id=run_id, strategy=("full_source_" + strategy) if single_shot else ("cumulative_" + strategy) if cumulative_inputs is not None else strategy,
                    worker_id=claimed_by, attempts=1 if claimed_by else 0))
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

    def full_source_input(self, run_id: str, user_id: str) -> dict[str, Any]:
        """仅供 Worker 读取本人冻结输入；普通历史投影和比较 SSE 不暴露源码正文。"""
        with self.session_factory() as session:
            row = session.query(ExperimentAuditRecordModel.payload).join(ExperimentAuditSessionModel,
                ExperimentAuditSessionModel.run_id == ExperimentAuditRecordModel.run_id).filter(
                ExperimentAuditSessionModel.user_id == user_id, ExperimentAuditRecordModel.run_id == run_id,
                ExperimentAuditSessionModel.status != "expired", ExperimentAuditRecordModel.kind == "input_snapshot").first()
            if row is None:
                raise ValueError("完整源码实验缺少冻结输入；不能退回其他协议。")
            return row[0]

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
                created_at=row.created_at,
            )
        finally:
            session.close()

    def list_for_project(self, project_id: str, user_id: str, *, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """分页返回项目的盲态摘要，包含所有终态；不加载回答、事件、工具或审计正文。"""
        baseline, security = aliased(AgentRunModel), aliased(AgentRunModel)
        with self.session_factory() as session:
            query = session.query(ExperimentComparisonModel).filter_by(project_id=project_id, user_id=user_id)
            total = query.count()
            reviewed = session.query(ExperimentReviewModel.preferred_lane).filter(
                ExperimentReviewModel.comparison_id == ExperimentComparisonModel.id,
                ExperimentReviewModel.user_id == user_id,
            ).limit(1).scalar_subquery()
            rows = query.with_entities(
                ExperimentComparisonModel.id, ExperimentComparisonModel.created_at,
                ExperimentComparisonModel.question, ExperimentComparisonModel.blind_order,
                baseline.status, baseline.model, baseline.max_steps,
                security.status, security.model, security.max_steps, reviewed,
            ).outerjoin(baseline, (baseline.id == ExperimentComparisonModel.baseline_run_id) & (baseline.user_id == user_id)).outerjoin(
                security, (security.id == ExperimentComparisonModel.graph_run_id) & (security.user_id == user_id),
            ).order_by(ExperimentComparisonModel.created_at.desc(), ExperimentComparisonModel.id.desc()).offset(offset).limit(limit).all()
            items = []
            for row in rows:
                runs = {"baseline": {"status": row[4], "model": row[5], "max_steps": row[6]},
                        "security_evidence": {"status": row[7], "model": row[8], "max_steps": row[9]}}
                runs["graph"] = runs["security_evidence"]  # 仅为读取旧记录，不创建旧图实验。
                items.append({"id": row[0], "created_at": row[1], "question_preview": row[2][:240],
                              "lanes": {lane: runs.get(strategy, {}) for lane, strategy in row[3].items()},
                              "reviewed": row[10] is not None, "preferred_lane": row[10]})
            return {"items": items, "total": total, "offset": offset, "limit": limit,
                    "has_more": offset + len(items) < total}

    def get_review(self, comparison_id: str, user_id: str) -> dict[str, Any] | None:
        """读取已保存的评分和备注；不保存新的评审或改变揭盲状态。"""
        with self.session_factory() as session:
            row = session.query(ExperimentReviewModel).filter_by(comparison_id=comparison_id, user_id=user_id).first()
            return {"preferred_lane": row.preferred_lane, "scores": row.scores, "notes": row.notes} if row else None

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
                "scores": {"rubric_version": review.rubric_version,
                           "left": review.left.model_dump(), "right": review.right.model_dump()},
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
            session.query(ExperimentAuditSessionModel).filter_by(comparison_id=comparison_id, user_id=user_id).update(
                {"protected": True}, synchronize_session=False,
            )
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
