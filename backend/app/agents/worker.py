"""持久化智能体队列 Worker；可嵌入 FastAPI，也可作为独立进程运行。"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.context_builder import ProjectContextBuilder
from backend.app.agents.orchestrator import AGENT_INSTRUCTIONS, AgentRunManager
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.tools import create_project_tool_registry
from backend.app.experiments.graph_context import GraphAugmentedContextBuilder
from backend.app.models import init_db
from backend.app.services.artifact_store import load_analysis_artifact
from backend.app.services.project_analysis.repository import ProjectRepository


class AgentQueueWorker:
    """顺序认领只读分析任务，并用数据库租约协调取消与崩溃恢复。"""

    def __init__(
        self,
        *,
        worker_id: str,
        store: AgentRunStore | None = None,
        projects: ProjectRepository | None = None,
        poll_seconds: float = 0.5,
        lease_seconds: int = 30,
    ):
        self.worker_id = worker_id
        self.store = store or AgentRunStore()
        self.projects = projects or ProjectRepository()
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
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds)
        return await asyncio.to_thread(self.store.recover_stale, cutoff)

    async def run_once(self) -> bool:
        """原子认领并执行一个任务；队列为空时返回 False。"""
        claim = await asyncio.to_thread(self.store.claim_next, self.worker_id)
        if claim is None:
            return False
        try:
            project = await asyncio.to_thread(
                self.projects.get_owned, claim.project_id, claim.user_id,
            )
            artifact = await asyncio.to_thread(load_analysis_artifact, claim.project_id)
            if project is None or artifact is None:
                self.store.fail_claim(
                    claim.run_id,
                    "Project workspace or analysis artifact is unavailable.",
                )
                return True
            manager, prepared_artifact = self._manager_for(claim.strategy, artifact)
            request = AgentRunRequest(
                question=claim.question,
                use_model=claim.use_model,
                max_steps=claim.max_steps,
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
            self.store.fail_claim(claim.run_id, "Agent worker failed before analysis completed.")
        finally:
            await asyncio.to_thread(self.store.finish_job, claim.run_id, self.worker_id)
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
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def _manager_for(self, strategy: str, artifact: dict) -> tuple[AgentRunManager, dict]:
        if strategy == "graph":
            return AgentRunManager(
                store=self.store,
                context_builder=GraphAugmentedContextBuilder(),
                tools=create_project_tool_registry(),
                instructions=AGENT_INSTRUCTIONS,
            ), artifact
        if strategy == "baseline":
            # TEMPORARY CONTROL GROUP / 临时对照组：
            # 图增强胜出后连同 baseline 目录与此分支一起删除。
            from backend.app.experiments.baseline.context_builder import BaselineContextBuilder
            from backend.app.experiments.baseline.strategy import (
                BASELINE_INSTRUCTIONS,
                prepare_baseline_artifact,
            )
            from backend.app.experiments.baseline.tool_registry import create_baseline_tool_registry

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
    raw = os.getenv("AGENT_WORKER_ID", "").strip()
    if raw:
        return raw[:64]
    host = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in socket.gethostname())
    return ((host or "worker") + "-agent")[:64]


agent_queue_worker = AgentQueueWorker(worker_id=default_worker_id())


async def _main(arguments) -> None:
    init_db()
    worker = AgentQueueWorker(
        worker_id=default_worker_id(),
        poll_seconds=arguments.poll_seconds,
        lease_seconds=arguments.lease_seconds,
    )
    await worker.recover_stale()
    if arguments.once:
        await worker.run_once()
    else:
        await worker.run_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Code Explorer persistent agent worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--lease-seconds", type=int, default=30)
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
