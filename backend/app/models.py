# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./database.sqlite"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ProjectModel(Base):
    """持久化一个已导入项目。

    输入字段为项目标识、用户标识、来源地址、本地工作目录和文件树；查询该模型
    输出项目元数据，源代码与分析产物本身不存入该表。
    """
    __tablename__ = "projects"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(
        String, index=True, default="default_user"
    )  # 预留的用户隔离字段
    repo_url = Column(String, nullable=False)
    local_path = Column(String, nullable=False)
    file_tree = Column(JSON, nullable=True)  # 存储静态文件树
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentRunModel(Base):
    """持久化一次智能体运行及其最终状态。

    输入为项目、用户、问题、模型开关和最大步骤数；输出为运行状态、模型信息、
    最终答案或错误，增量过程由 :class:`AgentEventModel` 单独保存。
    """
    __tablename__ = "agent_runs"

    id = Column(String, primary_key=True, index=True)
    project_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    question = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="queued", index=True)
    use_model = Column(Boolean, nullable=False, default=True)
    max_steps = Column(Integer, nullable=False, default=4)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    answer = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AgentJobModel(Base):
    """智能体持久化队列元数据；运行结果仍由 AgentRunModel 作为唯一公开状态。"""

    __tablename__ = "agent_jobs"

    run_id = Column(String, primary_key=True, index=True)
    strategy = Column(String, nullable=False, default="default", index=True)
    worker_id = Column(String, nullable=True, index=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentEventModel(Base):
    """持久化智能体运行中的一条有序事件。

    输入为运行 ID、单调递增序号、事件类型和 JSON 载荷；输出用于轮询、SSE
    断线续传及运行审计。
    """
    __tablename__ = "agent_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ExperimentComparisonModel(Base):
    """持久化一组图增强与临时无图对照运行的盲态配对关系。"""

    __tablename__ = "experiment_comparisons"

    id = Column(String, primary_key=True, index=True)
    project_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    question = Column(Text, nullable=False)
    # TEMPORARY CONTROL GROUP / 临时对照组：图增强胜出后随实验表迁移删除。
    baseline_run_id = Column(String, nullable=False, index=True)
    graph_run_id = Column(String, nullable=False, index=True)
    blind_order = Column(JSON, nullable=False)
    execution_order = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ExperimentReviewModel(Base):
    """持久化揭盲前的人工偏好与评分；不参与智能体正式运行。"""

    __tablename__ = "experiment_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comparison_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    preferred_lane = Column(String, nullable=False)
    scores = Column(JSON, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ExecutionTaskModel(Base):
    """持久化由独立 Worker 认领的隔离容器任务。"""

    __tablename__ = "execution_tasks"

    id = Column(String, primary_key=True, index=True)
    project_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    image = Column(String, nullable=False)
    argv = Column(JSON, nullable=False)
    scan_profile = Column(String, nullable=True)
    status = Column(String, nullable=False, default="queued", index=True)
    timeout_seconds = Column(Integer, nullable=False)
    cpu_limit = Column(String, nullable=False)
    memory_mb = Column(Integer, nullable=False)
    pids_limit = Column(Integer, nullable=False)
    worker_id = Column(String, nullable=True)
    exit_code = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    output_truncated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ExecutionEventModel(Base):
    """保存执行任务状态转换、策略决定和有界输出日志。"""

    __tablename__ = "execution_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db() -> None:
    """创建尚不存在的数据库表；无输入且无返回值。"""
    Base.metadata.create_all(bind=engine)
