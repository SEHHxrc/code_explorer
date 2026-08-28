"""创建、查询、盲评并揭示依赖图 A/B 配对实验。"""

from __future__ import annotations

import random
import uuid

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.experiments.contracts import BlindReviewRequest, ComparisonRequest, ExperimentError
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.experiments.repository import ComparisonRecord, ExperimentRepository
from backend.app.llm.registry import get_model_configuration
from backend.app.services.artifact_store import load_analysis_artifact
from backend.app.services.project_analysis.repository import ProjectRepository

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ExperimentComparisonService:
    """保持问题、模型和步骤预算一致，只改变依赖图输入与图工具。"""

    def __init__(self, *, repository=None, projects=None, run_store=None):
        self.repository = repository or ExperimentRepository()
        self.projects = projects or ProjectRepository()
        self.run_store = run_store or AgentRunStore()

    def create(self, project_id: str, user_id: str, request: ComparisonRequest) -> dict:
        config = get_model_configuration()
        if not config.configured:
            raise ExperimentError("Configure an online or local model before starting an A/B comparison.", 409)
        project = self.projects.get_owned(project_id, user_id)
        if project is None:
            raise ExperimentError("Project not found or unauthorized.", 404)
        artifact = load_analysis_artifact(project_id)
        if not artifact:
            raise ExperimentError("Project analysis artifact is missing.", 409)
        comparison_id = uuid.uuid4().hex
        # TEMPORARY CONTROL GROUP / 临时对照组：baseline run 仅服务于本配对实验。
        run_ids = {"baseline": uuid.uuid4().hex, "graph": uuid.uuid4().hex}
        agent_request = AgentRunRequest(question=request.question, use_model=True, max_steps=request.max_steps)
        # TEMPORARY CONTROL GROUP / 临时对照组：
        # baseline 策略随队列项持久化；图增强胜出后删除此运行创建和 strategy="baseline"。
        self.run_store.create(
            run_id=run_ids["baseline"], project_id=project_id, user_id=user_id,
            request=agent_request, strategy="baseline",
        )
        self.run_store.create(
            run_id=run_ids["graph"], project_id=project_id, user_id=user_id,
            request=agent_request, strategy="graph",
        )
        strategies = ["baseline", "graph"]
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
            graph_run_id=run_ids["graph"],
            blind_order=blind_order,
            execution_order=strategies,
        )
        self.repository.create(record)
        return self.get(comparison_id, user_id)

    def get(self, comparison_id: str, user_id: str) -> dict:
        record = self._record(comparison_id, user_id)
        runs = {
            "baseline": self.run_store.get(record.baseline_run_id, user_id),
            "graph": self.run_store.get(record.graph_run_id, user_id),
        }
        lanes = {}
        for lane, strategy in record.blind_order.items():
            run = runs[strategy]
            lanes[lane] = {
                "run": run.model_dump() if run else None,
                "metrics": collect_run_metrics(run.id, user_id) if run else {},
            }
        statuses = [item["run"]["status"] for item in lanes.values() if item["run"]]
        status = "completed" if statuses and all(item in TERMINAL_STATUSES for item in statuses) else "running"
        return {
            "id": record.comparison_id,
            "project_id": record.project_id,
            "question": record.question,
            "status": status,
            "lanes": lanes,
            "reviewed": self.repository.has_review(comparison_id, user_id),
            "events_url": f"/api/experiments/comparisons/{comparison_id}/events",
        }

    def review(self, comparison_id: str, user_id: str, review: BlindReviewRequest) -> dict:
        view = self.get(comparison_id, user_id)
        if view["status"] != "completed":
            raise ExperimentError("Both experiment runs must finish before blind review.", 409)
        self.repository.save_review(comparison_id, user_id, review)
        return {"comparison_id": comparison_id, "reviewed": True, "reveal": self.reveal(comparison_id, user_id)}

    def reveal(self, comparison_id: str, user_id: str) -> dict:
        record = self._record(comparison_id, user_id)
        if not self.repository.has_review(comparison_id, user_id):
            raise ExperimentError("Submit a blind review before revealing experiment groups.", 409)
        return {"left": record.blind_order["left"], "right": record.blind_order["right"]}

    def _record(self, comparison_id: str, user_id: str) -> ComparisonRecord:
        record = self.repository.get(comparison_id, user_id)
        if record is None:
            raise ExperimentError("Experiment comparison not found.", 404)
        return record
