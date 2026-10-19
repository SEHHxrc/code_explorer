"""实验审计数据库边界：与项目删除共用事务，不另建可能遗留的正文文件。"""

import json
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.app.models import ExperimentAuditSessionModel, ExperimentAuditRecordModel, AgentRunModel, AgentEventModel, SessionLocal
from .policy import AuditError, AuditLimits


def lock_writes(session: Session) -> None:
    """SQLite 在配额检查前串行化写事务，防止并发请求同时占用同一剩余配额。"""
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


class AuditRepository:
    """只管理实验审计；所有读取同时验证运行所属用户，不公开盲态内容。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal, limits: AuditLimits | None = None) -> None:
        """注入会话与存储配额，用于持久化运行和隔离测试。"""
        self.session_factory = session_factory
        self.limits = limits or AuditLimits.from_environment()

    def reserve_pair(self, session: Session, comparison_id: str, project_id: str, user_id: str, run_ids: list[str], contract_hash: str | dict[str, str] = "", *, input_snapshots: dict[str, dict[str, Any]] | None = None) -> None:
        """在创建配对的同一写事务中为两组预留配额；不足时不创建任何运行。"""
        used = session.query(func.sum(ExperimentAuditSessionModel.reserved_bytes)).scalar() or 0
        if used + len(run_ids) * self.limits.run_bytes > self.limits.total_bytes:
            raise AuditError("实验审计存储配额不足，请清理或提高配额后再创建配对。")
        for run_id in run_ids:
            snapshot = input_snapshots[run_id] if input_snapshots is not None else None
            size = len(json.dumps(snapshot).encode("utf-8")) if snapshot is not None else 0
            if size > min(self.limits.record_bytes, self.limits.run_bytes):
                raise AuditError("完整源码冻结输入超过审计存储上限；未创建配对。")
            session.add(ExperimentAuditSessionModel(
                run_id=run_id, project_id=project_id, user_id=user_id, comparison_id=comparison_id,
                contract_hash=contract_hash[run_id] if isinstance(contract_hash, dict) else contract_hash,
                reserved_bytes=self.limits.run_bytes, payload_bytes=size,
                expires_at=datetime.now(timezone.utc) + timedelta(days=self.limits.retention_days),
            ))
            if snapshot is not None:
                session.add(ExperimentAuditRecordModel(run_id=run_id, kind="input_snapshot", payload=snapshot))

    def exists(self, run_id: str, user_id: str) -> bool:
        """只有新实验的预留记录才启用审计；旧记录不补写。"""
        with self.session_factory() as session:
            return session.query(ExperimentAuditSessionModel).filter_by(run_id=run_id, user_id=user_id).first() is not None

    def contract(self, run_id: str, user_id: str) -> str:
        """读取创建配对时冻结的契约，避免 API 与 Worker 版本不一致却仍发请求。"""
        with self.session_factory() as session:
            row = session.query(ExperimentAuditSessionModel).filter_by(run_id=run_id, user_id=user_id).first()
            return row.contract_hash if row else ""

    def append(self, run_id: str, user_id: str, kind: str, payload: dict[str, Any]) -> int:
        """按 ORM JSON 表示计量，完整原子追加；超限不写半条记录。"""
        size = len(json.dumps(payload).encode("utf-8"))
        if size > self.limits.record_bytes:
            raise AuditError("单条审计超过存储上限，未静默截断；本次运行已停止。")
        with self.session_factory() as session:
            lock_writes(session)
            owner = session.query(ExperimentAuditSessionModel).filter_by(run_id=run_id, user_id=user_id).first()
            if owner is None or owner.status in {"expired", "complete", "incomplete"}:
                raise AuditError("审计会话不可写。")
            if owner.payload_bytes + size > owner.reserved_bytes:
                raise AuditError("运行审计配额耗尽，未静默省略内容；本次运行已停止。")
            row = ExperimentAuditRecordModel(run_id=run_id, kind=kind, payload=payload)
            session.add(row)
            owner.payload_bytes += size
            owner.status = "recording"
            session.flush()
            identifier = row.id
            session.commit()
            return identifier

    def finish(self, run_id: str, user_id: str, *, complete: bool) -> None:
        """终态释放未使用预留量；保留已存正文和明确的审计完整性。"""
        with self.session_factory() as session:
            lock_writes(session)
            row = session.query(ExperimentAuditSessionModel).filter_by(run_id=run_id, user_id=user_id).first()
            if row is not None and row.status != "expired":
                row.status = "complete" if complete else "incomplete"
                row.reserved_bytes = row.payload_bytes
                session.commit()

    def read(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """返回有权读取的审计；HTTP 层还须限制本机、显式启用和揭盲条件。"""
        with self.session_factory() as session:
            owner = session.query(ExperimentAuditSessionModel).filter_by(run_id=run_id, user_id=user_id).first()
            if owner is None:
                return None
            rows = session.query(ExperimentAuditRecordModel).filter_by(run_id=run_id).order_by(ExperimentAuditRecordModel.id).all()
            return {"status": owner.status, "payload_bytes": owner.payload_bytes, "protected": owner.protected,
                    "records": [{"id": row.id, "kind": row.kind, "payload": row.payload} for row in rows]}

    def prompt_snapshot(self, run_id: str, user_id: str) -> dict[str, Any]:
        """仅投影已保存的共同指令及指纹，供本人历史比较；不返回请求、源码、工具结果或组别。"""
        fields = ("instructions", "instructions_sha256", "report_version", "tools_sha256",
                  "files_sha256", "static_artifact_sha256", "execution_policy")
        with self.session_factory() as session:
            snapshot = session.query(*(AgentEventModel.payload[key] for key in fields)).join(
                AgentRunModel, AgentRunModel.id == AgentEventModel.run_id,
            ).filter(AgentRunModel.user_id == user_id, AgentEventModel.run_id == run_id,
                     AgentEventModel.event_type == "experiment.snapshot").order_by(AgentEventModel.sequence).first()
            if snapshot:
                return dict(zip(fields, snapshot))
            row = session.query(*(ExperimentAuditRecordModel.payload[key] for key in fields)).join(
                ExperimentAuditSessionModel, ExperimentAuditSessionModel.run_id == ExperimentAuditRecordModel.run_id,
            ).filter(ExperimentAuditSessionModel.user_id == user_id, ExperimentAuditRecordModel.run_id == run_id,
                     ExperimentAuditRecordModel.kind == "manifest").order_by(ExperimentAuditRecordModel.id).first()
            return dict(zip(fields, row)) if row else {"status": "unavailable"}

    def cleanup_expired(self) -> int:
        """清理未保护且运行已终止的过期正文，保留会话过期标记，不动旧事件。"""
        with self.session_factory() as session:
            lock_writes(session)
            rows = session.query(ExperimentAuditSessionModel).join(AgentRunModel, AgentRunModel.id == ExperimentAuditSessionModel.run_id).filter(
                ExperimentAuditSessionModel.expires_at < datetime.now(timezone.utc),
                ExperimentAuditSessionModel.protected.is_(False),
                ExperimentAuditSessionModel.status != "expired",
                AgentRunModel.status.in_({"completed", "failed", "cancelled"}),
            ).all()
            for row in rows:
                session.query(ExperimentAuditRecordModel).filter_by(run_id=row.run_id).delete(synchronize_session=False)
                row.status, row.payload_bytes, row.reserved_bytes = "expired", 0, 0
            session.commit()
            return len(rows)
