"""项目元数据的持久化边界。"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.models import (
    AgentEventModel,
    AgentRunModel,
    ExperimentComparisonModel,
    ExperimentReviewModel,
    ProjectModel,
    SessionLocal,
)


@dataclass(frozen=True)
class ProjectRecord:
    """脱离 SQLAlchemy 会话的项目元数据快照。"""

    project_id: str
    user_id: str
    source: str
    local_path: str


class ProjectRepository:
    """在仓储内部创建和关闭会话，避免路由或服务泄漏事务所有权。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self._session_factory = session_factory

    def create(self, *, project_id: str, user_id: str, source: str, local_path: str, file_tree: list[dict]) -> None:
        """持久化项目元数据；失败时回滚并向上抛出。"""
        session = self._session_factory()
        try:
            session.add(ProjectModel(
                id=project_id,
                user_id=user_id,
                repo_url=source,
                local_path=local_path,
                file_tree=file_tree,
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_owned(self, project_id: str, user_id: str) -> ProjectRecord | None:
        """输出属于用户的项目快照，避免向服务层泄漏活动会话。"""
        session = self._session_factory()
        try:
            project = session.query(ProjectModel).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first()
            if project is None:
                return None
            return ProjectRecord(
                project_id=project.id,
                user_id=project.user_id,
                source=project.repo_url,
                local_path=project.local_path,
            )
        finally:
            session.close()

    def has_active_runs(self, project_id: str, user_id: str) -> bool:
        """项目存在排队或运行中的智能体任务时输出 True。"""
        session = self._session_factory()
        try:
            return session.query(AgentRunModel.id).filter(
                AgentRunModel.project_id == project_id,
                AgentRunModel.user_id == user_id,
                AgentRunModel.status.in_(("queued", "running")),
            ).first() is not None
        finally:
            session.close()

    def delete_owned_with_runs(self, project_id: str, user_id: str) -> bool:
        """在一个数据库事务中删除项目及其智能体运行和事件。"""
        session = self._session_factory()
        try:
            project = session.query(ProjectModel).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first()
            if project is None:
                return False
            # TEMPORARY CONTROL GROUP / 临时对照组：先清理配对实验，防止项目删除后残留记录。
            comparison_ids = [row[0] for row in session.query(ExperimentComparisonModel.id).filter(
                ExperimentComparisonModel.project_id == project_id,
                ExperimentComparisonModel.user_id == user_id,
            ).all()]
            if comparison_ids:
                session.query(ExperimentReviewModel).filter(
                    ExperimentReviewModel.comparison_id.in_(comparison_ids)
                ).delete(synchronize_session=False)
                session.query(ExperimentComparisonModel).filter(
                    ExperimentComparisonModel.id.in_(comparison_ids)
                ).delete(synchronize_session=False)
            run_ids = [row[0] for row in session.query(AgentRunModel.id).filter(
                AgentRunModel.project_id == project_id,
                AgentRunModel.user_id == user_id,
            ).all()]
            if run_ids:
                session.query(AgentEventModel).filter(
                    AgentEventModel.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
                session.query(AgentRunModel).filter(
                    AgentRunModel.id.in_(run_ids)
                ).delete(synchronize_session=False)
            session.delete(project)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
