"""项目元数据及跨功能域删除事务的持久化边界。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import (
    AgentEventModel,
    AgentJobModel,
    AgentRunModel,
    ExecutionEventModel,
    ExecutionTaskModel,
    ExperimentComparisonModel,
    ExperimentReviewModel,
    ProjectModel,
    SessionLocal,
)

from .contracts import ProjectRecord, ProjectUpdate


def _record(project: ProjectModel) -> ProjectRecord:
    """把活动 SQLAlchemy 模型转换为脱离会话的项目记录。"""
    return ProjectRecord(
        project_id=project.id,
        user_id=project.user_id,
        source=project.repo_url,
        local_path=project.local_path,
        file_tree=project.file_tree or [],
        created_at=project.created_at,
    )


class ProjectRepository:
    """只管理 ProjectModel，不操作 Agent、Execution 或 Experiment 表。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """使用可替换的会话工厂初始化项目仓储。"""
        self._session_factory = session_factory

    def create(
        self,
        *,
        project_id: str,
        user_id: str,
        source: str,
        local_path: str,
        file_tree: list[dict[str, Any]],
    ) -> ProjectRecord:
        """新增项目记录；项目 ID 冲突时失败，不执行隐式更新。"""
        session = self._session_factory()
        try:
            project = ProjectModel(
                id=project_id,
                user_id=user_id,
                repo_url=source,
                local_path=local_path,
                file_tree=file_tree,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return _record(project)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_owned(self, project_id: str, user_id: str) -> ProjectRecord | None:
        """返回用户拥有的项目快照，不泄漏活动数据库会话。"""
        session = self._session_factory()
        try:
            project = session.query(ProjectModel).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first()
            return _record(project) if project is not None else None
        finally:
            session.close()

    def list_owned(self, user_id: str) -> list[ProjectRecord]:
        """按创建时间倒序返回用户拥有的全部项目。"""
        session = self._session_factory()
        try:
            projects = session.query(ProjectModel).filter(
                ProjectModel.user_id == user_id,
            ).order_by(ProjectModel.created_at.desc()).all()
            return [_record(project) for project in projects]
        finally:
            session.close()

    def update_owned(
        self,
        project_id: str,
        user_id: str,
        changes: ProjectUpdate,
    ) -> ProjectRecord | None:
        """按所有权更新白名单字段并返回更新后的项目快照。"""
        session = self._session_factory()
        try:
            project = session.query(ProjectModel).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first()
            if project is None:
                return None
            project.file_tree = changes.file_tree
            session.commit()
            session.refresh(project)
            return _record(project)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_owned(self, project_id: str, user_id: str) -> bool:
        """只删除用户拥有的 ProjectModel；关联域清理由删除协调仓储负责。"""
        session = self._session_factory()
        try:
            project = session.query(ProjectModel).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first()
            if project is None:
                return False
            session.delete(project)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class ProjectDeletionRepository:
    """在单一数据库事务中检查活动任务并删除项目关联记录。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """使用共享会话边界初始化跨功能域删除仓储。"""
        self._session_factory = session_factory

    def has_active_tasks(self, project_id: str, user_id: str) -> bool:
        """返回项目是否存在排队、运行或等待取消的任务。"""
        session = self._session_factory()
        try:
            agent_active = session.query(AgentRunModel.id).filter(
                AgentRunModel.project_id == project_id,
                AgentRunModel.user_id == user_id,
                AgentRunModel.status.in_(("queued", "running")),
            ).first() is not None
            if agent_active:
                return True
            return session.query(ExecutionTaskModel.id).filter(
                ExecutionTaskModel.project_id == project_id,
                ExecutionTaskModel.user_id == user_id,
                ExecutionTaskModel.status.in_(("queued", "running", "cancel_requested")),
            ).first() is not None
        finally:
            session.close()

    def delete_owned_with_dependents(self, project_id: str, user_id: str) -> bool:
        """在一个事务中删除项目及其 Agent、Execution 和 Experiment 记录。"""
        session = self._session_factory()
        try:
            project = session.query(ProjectModel).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first()
            if project is None:
                return False
            comparison_ids = [row[0] for row in session.query(ExperimentComparisonModel.id).filter(
                ExperimentComparisonModel.project_id == project_id,
                ExperimentComparisonModel.user_id == user_id,
            ).all()]
            if comparison_ids:
                session.query(ExperimentReviewModel).filter(
                    ExperimentReviewModel.comparison_id.in_(comparison_ids),
                ).delete(synchronize_session=False)
                session.query(ExperimentComparisonModel).filter(
                    ExperimentComparisonModel.id.in_(comparison_ids),
                ).delete(synchronize_session=False)
            execution_ids = [row[0] for row in session.query(ExecutionTaskModel.id).filter(
                ExecutionTaskModel.project_id == project_id,
                ExecutionTaskModel.user_id == user_id,
            ).all()]
            if execution_ids:
                session.query(ExecutionEventModel).filter(
                    ExecutionEventModel.task_id.in_(execution_ids),
                ).delete(synchronize_session=False)
                session.query(ExecutionTaskModel).filter(
                    ExecutionTaskModel.id.in_(execution_ids),
                ).delete(synchronize_session=False)
            run_ids = [row[0] for row in session.query(AgentRunModel.id).filter(
                AgentRunModel.project_id == project_id,
                AgentRunModel.user_id == user_id,
            ).all()]
            if run_ids:
                session.query(AgentEventModel).filter(
                    AgentEventModel.run_id.in_(run_ids),
                ).delete(synchronize_session=False)
                session.query(AgentJobModel).filter(
                    AgentJobModel.run_id.in_(run_ids),
                ).delete(synchronize_session=False)
                session.query(AgentRunModel).filter(
                    AgentRunModel.id.in_(run_ids),
                ).delete(synchronize_session=False)
            session.delete(project)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
