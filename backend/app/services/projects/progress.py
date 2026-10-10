"""当前 API 进程内、按用户隔离的临时导入进度；不替代事务日志。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.app.services.project_analysis.progress import ProgressReader

from .errors import ProjectAnalysisError

STAGES = {
    "awaiting_upload": "等待项目上传",
    "preparing": "获取、解压和清理项目源码",
    "dependency": "扫描和解析源码文件",
    "security_scan": "扫描安全入口、Source 和 Sink",
    "program_graph": "构建 CFG / DFG 并计算到达定义",
    "dataflow": "计算污点传播路径",
    "evidence": "组装安全候选和源码证据",
    "projection": "生成文件树、项目清单和展示图",
    "publishing": "发布项目工作区",
    "persisting": "保存分析产物和项目记录",
    "completed": "项目分析完成",
    "failed": "项目导入或分析失败",
}


@dataclass
class ImportProgress:
    """一次导入的观测状态；文件计数由原分析器实时提供。"""

    user_id: str
    started_at: float
    updated_at: float
    stage: str = "awaiting_upload"
    status: str = "pending"
    reader: ProgressReader | None = None
    message: str = ""
    project_id: str | None = None


class ImportProgressStore:
    """有容量和过期限制的进度登记表；活动任务不因查询间隔而过期。"""

    def __init__(
        self, *, ttl_seconds: float = 600, max_entries: int = 4096,
        max_per_user: int = 8, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """设置待上传/终态保留秒数、总容量、单用户活动配额和单调时钟。"""
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._max_per_user = max_per_user
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, ImportProgress] = {}

    def _prune(self, now: float) -> None:
        """在持锁状态下清理待上传和终态记录；不删除正在执行的任务。"""
        for request_id, entry in list(self._entries.items()):
            if entry.status != "running" and now - entry.updated_at >= self._ttl:
                del self._entries[request_id]

    def create(self, user_id: str) -> str:
        """为用户分配不可猜测的进度 ID；超过容量或活动配额时返回 429。"""
        now = self._clock()
        with self._lock:
            self._prune(now)
            active = sum(
                entry.user_id == user_id and entry.status in {"pending", "running"}
                for entry in self._entries.values()
            )
            if len(self._entries) >= self._max_entries or active >= self._max_per_user:
                raise ProjectAnalysisError("导入进度任务过多，请稍后重试。", status_code=429)
            request_id = uuid4().hex
            self._entries[request_id] = ImportProgress(user_id, now, now)
        return request_id

    def _owned(self, request_id: str, user_id: str) -> ImportProgress:
        """在持锁状态下验证所有权；不存在和越权使用相同的公开错误。"""
        entry = self._entries.get(request_id)
        if entry is None or entry.user_id != user_id:
            raise ProjectAnalysisError("分析进度不存在或无权访问。", status_code=404)
        return entry

    def claim(self, request_id: str, user_id: str) -> ImportProgressTracker:
        """只允许所属用户把待上传任务绑定到一次导入，防止 ID 重放。"""
        with self._lock:
            self._prune(self._clock())
            entry = self._owned(request_id, user_id)
            if entry.status != "pending":
                raise ProjectAnalysisError("该进度任务已用于项目导入。", status_code=409)
            entry.status = "running"
            entry.updated_at = self._clock()
        return ImportProgressTracker(self, request_id)

    def update(
        self, request_id: str, stage: str, *, reader: ProgressReader | None = None,
        message: str = "", project_id: str | None = None,
    ) -> None:
        """仅更新活动任务；切换阶段或到达终态时释放旧分析器读取器。"""
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None or entry.status != "running" or stage not in STAGES:
                return
            entry.stage = stage
            entry.updated_at = self._clock()
            entry.reader = reader
            entry.message = message
            entry.project_id = project_id
            if stage in {"completed", "failed"}:
                entry.status = stage

    def snapshot(self, request_id: str, user_id: str) -> dict[str, Any]:
        """输出当前用户的阶段、真实文件计数和耗时；不返回内部路径或源码。"""
        with self._lock:
            now = self._clock()
            self._prune(now)
            entry = self._owned(request_id, user_id)
            if entry.status == "pending":
                entry.updated_at = now  # 上传期间持续轮询续期，避免慢速大 ZIP 的进度 ID 过期。
            counts: dict[str, Any] = {}
            total = processed = 0
            # get_progress 只读两个整数，不做源码遍历；持锁避免阶段切换快照串台。
            if entry.reader is not None:
                try:
                    counts = entry.reader()
                    total = max(0, int(counts.get("total_files") or 0))
                    processed = min(total, max(0, int(counts.get("parsed_files") or 0)))
                except Exception:
                    counts = {}  # 计数暂不可用不等同于项目分析失败。
                    total = processed = 0
            return {
                "request_id": request_id, "status": entry.status,
                "stage": entry.stage, "stage_label": STAGES[entry.stage],
                "detail": str(counts.get("stage_label") or ""),
                "total_files": total, "processed_files": processed,
                "elapsed_seconds": round(
                    (entry.updated_at if entry.status in {"completed", "failed"} else now)
                    - entry.started_at, 1,
                ),
                "message": entry.message, "project_id": entry.project_id,
            }


class ImportProgressTracker:
    """向纯分析提供窄接口，由项目导入用例负责事务最终状态。"""

    def __init__(self, store: ImportProgressStore, request_id: str) -> None:
        """绑定已经认领的进度任务；不持有分析器本身。"""
        self._store = store
        self._request_id = request_id

    def phase(self, stage: str, reader: ProgressReader | None = None) -> None:
        """登记 stage 与可选实时读取器；阶段变更会移除旧读取器。"""
        self._store.update(self._request_id, stage, reader=reader)

    def complete(self, project_id: str) -> None:
        """仅在导入事务成功后登记完成和可恢复的项目 ID。"""
        self._store.update(self._request_id, "completed", project_id=project_id)

    def fail(self, message: str) -> None:
        """回滚处理后登记失败；message 必须是已经脱敏的公开消息。"""
        self._store.update(self._request_id, "failed", message=message)
