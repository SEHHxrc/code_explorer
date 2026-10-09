"""创建、查询、盲评并揭示静态安全证据 A/B 配对实验。"""

from __future__ import annotations

import random
import uuid

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.experiments.contracts import (
    BlindReviewRequest,
    ComparisonRequest,
    ExperimentError,
)
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.experiments.repository import ComparisonRecord, ExperimentRepository
from backend.app.llm.registry import get_model_configuration
from backend.app.services.projects import ProjectArtifactRepository, ProjectRepository

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ExperimentComparisonService:
    """保持问题、模型、指令、原始文件工具和预算一致，只改变静态安全证据输入。"""

    def __init__(
        self,
        *,
        repository: ExperimentRepository | None = None,
        projects: ProjectRepository | None = None,
        run_store: AgentRunStore | None = None,
        artifacts: ProjectArtifactRepository | None = None,
    ) -> None:
        """注入实验仓储、项目仓储与智能体运行仓储。"""
        self.repository = repository or ExperimentRepository()
        self.projects = projects or ProjectRepository()
        self.run_store = run_store or AgentRunStore(self.repository.session_factory)
        self.artifacts = artifacts or ProjectArtifactRepository()

    def create(self, project_id: str, user_id: str, request: ComparisonRequest) -> dict:
        """创建并持久化新的领域记录。"""
        config = get_model_configuration()
        if not config.configured:
            raise ExperimentError("Configure an online or local model before starting an A/B comparison.", 409)
        project = self.projects.get_owned(project_id, user_id)
        if project is None:
            raise ExperimentError("Project not found or unauthorized.", 404)
        artifact = self.artifacts.load(project_id)
        if not artifact:
            raise ExperimentError("Project analysis artifact is missing.", 409)
        if not artifact.get("security_evidence"):
            raise ExperimentError("Re-import this project to generate static security evidence before comparing.", 409)
        comparison_id = uuid.uuid4().hex
        # TEMPORARY CONTROL GROUP / 临时对照组：baseline run 仅服务于本配对实验。
        run_ids = {"baseline": uuid.uuid4().hex, "security_evidence": uuid.uuid4().hex}
        agent_request = AgentRunRequest(question=request.question, use_model=True, max_steps=request.max_steps, model=config.model)
        # TEMPORARY CONTROL GROUP / 临时对照组：
        # baseline 策略随队列项持久化；证据增强确认更优后删除此对照策略。
        strategies = ["baseline", "security_evidence"]
        random.shuffle(strategies)
        lanes = ["left", "right"]
        random.shuffle(lanes)
        blind_order = dict(zip(lanes, strategies))
        record = ComparisonRecord(
            comparison_id=comparison_id,
            project_id=project_id,
            user_id=user_id,
            question=request.question,
            # TEMPORARY CONTROL GROUP / 临时对照组：配对表中的无图运行引用。
            baseline_run_id=run_ids["baseline"],
            graph_run_id=run_ids["security_evidence"],  # 保留旧列名以兼容历史数据库，不表示图输入。
            blind_order=blind_order,
            execution_order=strategies,
        )
        self.repository.create_pair(record, agent_request)
        return self.get(comparison_id, user_id)

    def get(self, comparison_id: str, user_id: str) -> dict:
        """按标识和用户读取其有权访问的领域记录。"""
        record = self._record(comparison_id, user_id)
        runs = {
            "baseline": self.run_store.get(record.baseline_run_id, user_id),
            "graph": self.run_store.get(record.graph_run_id, user_id),
            "security_evidence": self.run_store.get(record.graph_run_id, user_id),
        }
        lanes = {}
        for lane, strategy in record.blind_order.items():
            run = runs[strategy]
            lanes[lane] = {
                "run": run.model_dump() if run else None,
                "metrics": collect_run_metrics(run.id, user_id, session_factory=self.run_store.session_factory) if run else {},
            }
        statuses = [item["run"]["status"] for item in lanes.values() if item["run"]]
        terminal = len(statuses) == 2 and all(item in TERMINAL_STATUSES for item in statuses)
        valid = terminal and all(item == "completed" for item in statuses)
        status = "completed" if terminal else "running"
        return {
            "id": record.comparison_id,
            "project_id": record.project_id,
            "question": record.question,
            "status": status,
            "valid_for_review": valid,
            "protocol": "static-security-v1" if "security_evidence" in record.execution_order else "legacy-graph",
            "lanes": lanes,
            "reviewed": self.repository.has_review(comparison_id, user_id),
            "events_url": f"/api/experiments/comparisons/{comparison_id}/events",
        }

    def review(self, comparison_id: str, user_id: str, review: BlindReviewRequest) -> dict:
        """校验并保存配对实验的盲评结果。"""
        view = self.get(comparison_id, user_id)
        if not view["valid_for_review"]:
            raise ExperimentError("Both model runs must succeed; failed or cancelled runs are not valid effectiveness trials.", 409)
        self.repository.save_review(comparison_id, user_id, review)
        return {"comparison_id": comparison_id, "reviewed": True, "reveal": self.reveal(comparison_id, user_id)}

    def reveal(self, comparison_id: str, user_id: str) -> dict:
        """在盲评完成后返回左右通道的真实身份。"""
        record = self._record(comparison_id, user_id)
        if not self.repository.has_review(comparison_id, user_id):
            raise ExperimentError("Submit a blind review before revealing experiment groups.", 409)
        return {"left": record.blind_order["left"], "right": record.blind_order["right"]}

    def _record(self, comparison_id: str, user_id: str) -> ComparisonRecord:
        """读取配对实验记录，不存在时抛出领域错误。"""
        record = self.repository.get(comparison_id, user_id)
        if record is None:
            raise ExperimentError("Experiment comparison not found.", 404)
        return record
