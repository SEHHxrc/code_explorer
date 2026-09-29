from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./database.sqlite"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """SQLAlchemy 2 声明式模型基类。"""


class ProjectModel(Base):
    """持久化一个已导入项目。

    输入字段为项目标识、用户标识、来源地址、本地工作目录和文件树；查询该模型
    输出项目元数据，源代码与分析产物本身不存入该表。
    """
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String, index=True, default="default_user"
    )  # 预留的用户隔离字段
    repo_url: Mapped[str] = mapped_column(String, nullable=False)
    local_path: Mapped[str] = mapped_column(String, nullable=False)
    file_tree: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class AgentRunModel(Base):
    """持久化一次智能体运行及其最终状态。

    输入为项目、用户、问题、模型开关和最大步骤数；输出为运行状态、模型信息、
    最终答案或错误，增量过程由 :class:`AgentEventModel` 单独保存。
    """
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued", index=True)
    use_model: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AgentJobModel(Base):
    """智能体持久化队列元数据；运行结果仍由 AgentRunModel 作为唯一公开状态。"""

    __tablename__ = "agent_jobs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    strategy: Mapped[str] = mapped_column(String, nullable=False, default="default", index=True)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class AgentEventModel(Base):
    """持久化智能体运行中的一条有序事件。

    输入为运行 ID、单调递增序号、事件类型和 JSON 载荷；输出用于轮询、SSE
    断线续传及运行审计。
    """
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

class ExperimentComparisonModel(Base):
    """持久化一组图增强与临时无图对照运行的盲态配对关系。"""

    __tablename__ = "experiment_comparisons"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # TEMPORARY CONTROL GROUP / 临时对照组：图增强胜出后随实验表迁移删除。
    baseline_run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    graph_run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    blind_order: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    execution_order: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ExperimentReviewModel(Base):
    """持久化揭盲前的人工偏好与评分；不参与智能体正式运行。"""

    __tablename__ = "experiment_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    preferred_lane: Mapped[str] = mapped_column(String, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

class ExecutionTaskModel(Base):
    """持久化由独立 Worker 认领的隔离容器任务。"""

    __tablename__ = "execution_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    image: Mapped[str] = mapped_column(String, nullable=False)
    argv: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    scan_profile: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued", index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_limit: Mapped[str] = mapped_column(String, nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    pids_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExecutionEventModel(Base):
    """保存执行任务状态转换、策略决定和有界输出日志。"""

    __tablename__ = "execution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


def init_db() -> None:
    """创建尚不存在的数据库表；无输入且无返回值。"""
    Base.metadata.create_all(bind=engine)
