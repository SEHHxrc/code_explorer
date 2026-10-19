"""持久化智能体队列 Worker；可嵌入 FastAPI，也可作为独立进程运行。"""

from __future__ import annotations

import argparse
import logging
import asyncio
from contextlib import suppress
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from backend.app.agents.context_builder import ProjectContextBuilder
from backend.app.agents.contracts import AgentClaim, AgentRunRequest
from backend.app.agents.orchestrator import AGENT_INSTRUCTIONS, AgentRunManager
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.tools import create_project_tool_registry
from backend.app.experiments.context import (
    SECURITY_EXPERIMENT_INSTRUCTIONS, SecurityExperimentContextBuilder, prepare_experiment_artifact,
)
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.models import init_db
from backend.app.core.logging import LOG_CONTEXT, configure_logging
from backend.app.services.projects import ProjectArtifactRepository, ProjectRepository

logger = logging.getLogger(__name__)


class AgentQueueWorker:
    """顺序认领只读分析任务，并用数据库租约协调取消与崩溃恢复。"""

    def __init__(
        self,
        *,
        worker_id: str,
        store: AgentRunStore | None = None,
        projects: ProjectRepository | None = None,
        artifacts: ProjectArtifactRepository | None = None,
        poll_seconds: float = 0.5,
        lease_seconds: int = 30,
    ) -> None:
        """注入运行仓储、项目仓储、编排策略与 Worker 租约配置。"""
        self.worker_id = worker_id
        self.store = store or AgentRunStore()
        self.projects = projects or ProjectRepository()
        self.artifacts = artifacts or ProjectArtifactRepository()
        self.poll_seconds = max(0.1, poll_seconds)
        self.lease_seconds = max(15, lease_seconds)
        self._loop_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    def start(self) -> None:
        """在当前事件循环中启动一个消费循环；重复调用幂等。"""
        if self._loop_task is None or self._loop_task.done():
            self._stop.clear()
            self._loop_task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        """停止领取新任务，并等待当前只读分析安全结束。"""
        self._stop.set()
        self._wake.set()
        if self._loop_task is not None:
            await self._loop_task
        self._loop_task = None

    def notify(self) -> None:
        """通知嵌入式 Worker 有新任务，轮询仍作为跨进程兜底。"""
        self._wake.set()

    async def recover_stale(self) -> list[str]:
        """补建旧队列项并终结租约过期的运行。"""
        await asyncio.to_thread(self.store.ensure_jobs)
        from backend.app.experiments.audit import AuditRepository
        await asyncio.to_thread(AuditRepository(self.store.session_factory).cleanup_expired)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds)
        return await asyncio.to_thread(self.store.recover_stale, cutoff)

    async def run_once(self) -> bool:
        """原子认领并执行一个任务；队列为空时返回 False。"""
        claim = await asyncio.to_thread(self.store.claim_next, self.worker_id)
        if claim is None:
            return False
        return await self.execute_claim(claim)

    async def execute_claim(self, claim: AgentClaim) -> bool:
        """执行已由本 Worker 认领的任务；实验诊断复用此路径，不另写模型/审计循环。"""
        context_token = LOG_CONTEXT.set({**LOG_CONTEXT.get(), "run_id": claim.run_id,
                                         "project_id": claim.project_id, "worker_id": self.worker_id})
        logger.info("agent.job_claimed")
        try:
            project = await asyncio.to_thread(
                self.projects.get_owned, claim.project_id, claim.user_id,
            )
            artifact = await asyncio.to_thread(self.artifacts.load, claim.project_id)
            if project is None or artifact is None:
                self.store.fail_claim(
                    claim.run_id,
                    "Project workspace or analysis artifact is unavailable.",
                )
                return True
            full_source = claim.strategy.startswith("full_source_")
            cumulative = claim.strategy.startswith("cumulative_")
            if full_source or cumulative:
                from backend.app.experiments.repository import ExperimentRepository
                from backend.app.experiments.full_source import FullSourceContextBuilder, FULL_SOURCE_INSTRUCTIONS
                from backend.app.agents.tools.base import ToolRegistry
                prepared_artifact = await asyncio.to_thread(ExperimentRepository(self.store.session_factory).full_source_input, claim.run_id, claim.user_id)
                if cumulative:
                    from backend.app.experiments.cumulative import CumulativeContextBuilder, INSTRUCTIONS, tool_registry
                    manager = AgentRunManager(store=self.store, context_builder=CumulativeContextBuilder(),
                        tools=tool_registry(prepared_artifact["_cumulative_input"]), instructions=INSTRUCTIONS)
                else:
                    manager = AgentRunManager(store=self.store, context_builder=FullSourceContextBuilder(),
                                              tools=ToolRegistry([]), instructions=FULL_SOURCE_INSTRUCTIONS)
            else:
                manager, prepared_artifact = self._manager_for(claim.strategy, artifact)
            if full_source or cumulative or claim.strategy in {"baseline", "security_evidence"}:
                from backend.app.experiments.audit import AuditRepository
                from backend.app.experiments.audit.recorder import AuditRecorder
                audit_repository = AuditRepository(self.store.session_factory)
                if await asyncio.to_thread(audit_repository.exists, claim.run_id, claim.user_id):
                    manager.audit = AuditRecorder(audit_repository, claim.run_id, claim.user_id)
                    manager.audit_artifact = prepared_artifact if full_source or cumulative else artifact  # 仅供哈希；绝不加入 baseline 模型上下文。
            request = AgentRunRequest(
                question=claim.question,
                use_model=claim.use_model,
                max_steps=claim.max_steps,
                model=claim.model,
            )
            execution = asyncio.create_task(manager._run(
                run_id=claim.run_id,
                project_id=claim.project_id,
                user_id=claim.user_id,
                project_root=project.local_path,
                artifact=prepared_artifact,
                request=request,
                claimed=True,
            ))
            while not execution.done():
                done, _ = await asyncio.wait({execution}, timeout=1.0)
                if done:
                    break
                await asyncio.to_thread(self.store.heartbeat, claim.run_id, self.worker_id)
                if await asyncio.to_thread(self.store.is_cancel_requested, claim.run_id):
                    execution.cancel()
            await execution
        except Exception:
            logger.exception("agent.worker_failed")
            self.store.fail_claim(claim.run_id, "Agent worker failed before analysis completed.")
        finally:
            try:
                await asyncio.to_thread(self.store.finish_job, claim.run_id, self.worker_id)
            finally:
                LOG_CONTEXT.reset(context_token)
        return True

    async def run_forever(self) -> None:
        """持续消费持久化队列，并周期性回收过期租约。"""
        await self.recover_stale()
        last_recovery = time.monotonic()
        while not self._stop.is_set():
            self._wake.clear()
            processed = await self.run_once()
            if time.monotonic() - last_recovery >= self.lease_seconds:
                await self.recover_stale()
                last_recovery = time.monotonic()
            if processed:
                continue
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)

    def _manager_for(self, strategy: str, artifact: dict) -> tuple[AgentRunManager, dict]:
        """按运行策略创建隔离的智能体编排器。"""
        if strategy == "graph":
            raise ValueError("Legacy graph experiment cannot be resumed; create a static security comparison instead")
        if strategy == "security_evidence":
            return AgentRunManager(
                store=self.store,
                context_builder=SecurityExperimentContextBuilder(),
                tools=create_experiment_tool_registry(with_evidence=True),
                instructions=SECURITY_EXPERIMENT_INSTRUCTIONS,
            ), prepare_experiment_artifact(artifact, with_evidence=True)
        if strategy == "baseline":
            # TEMPORARY CONTROL GROUP / 临时对照组：
            # 安全证据增强确认更优并结束对照实验后，删除 baseline 目录及此分支。
            from backend.app.experiments.baseline.context_builder import (
                BaselineContextBuilder,
            )
            from backend.app.experiments.baseline.strategy import (
                BASELINE_INSTRUCTIONS,
                prepare_baseline_artifact,
            )
            from backend.app.experiments.baseline.tool_registry import (
                create_baseline_tool_registry,
            )

            return AgentRunManager(
                store=self.store,
                context_builder=BaselineContextBuilder(),
                tools=create_baseline_tool_registry(),
                instructions=BASELINE_INSTRUCTIONS,
            ), prepare_baseline_artifact(artifact)
        return AgentRunManager(
            store=self.store,
            context_builder=ProjectContextBuilder(),
            tools=create_project_tool_registry(),
            instructions=AGENT_INSTRUCTIONS,
        ), artifact


def default_worker_id() -> str:
    """生成包含主机和进程信息的默认 Worker 标识。"""
    raw = os.getenv("AGENT_WORKER_ID", "").strip()
    if raw:
        return raw[:64]
    host = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in socket.gethostname())
    return ((host or "worker") + "-agent")[:64]


agent_queue_worker = AgentQueueWorker(worker_id=default_worker_id())


async def _main(arguments: argparse.Namespace) -> None:
    """解析命令行参数并启动异步 Worker 主循环。"""
    init_db()
    worker = AgentQueueWorker(
        worker_id=default_worker_id(),
        poll_seconds=arguments.poll_seconds,
        lease_seconds=arguments.lease_seconds,
    )
    if arguments.once:
        await worker.recover_stale()
        await worker.run_once()
    else:
        await worker.run_forever()


def main() -> None:
    """启动当前模块对应的命令行 Worker 入口。"""
    configure_logging("agent-worker")
    parser = argparse.ArgumentParser(description="Code Explorer persistent agent worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--lease-seconds", type=int, default=30)
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
