"""完整项目导入→安全证据→模型/工具队列→盲评→历史恢复→删除的功能验证。

模型使用确定性替身，不产生 API 费用；本测试证明流程成立，不证明实验组效果更好。
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.worker import AgentQueueWorker
from backend.app.core.deps import get_current_user
from backend.app.experiments.contracts import (
    BlindReviewRequest,
    ComparisonRequest,
    ExperimentError,
    LaneScores,
)
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.experiments.repository import ExperimentRepository
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.llm.base import ModelProvider, ModelResult, ModelTurn, ToolCall
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.experiments.report import REPORT_END
from backend.app.llm.registry import ModelConfiguration
from backend.app.models import (
    AgentJobModel,
    AgentRunModel,
    Base,
    ExperimentComparisonModel,
)
from backend.app.services.projects import (
    AnalyzeProjectCommand,
    ProjectArtifactRepository,
    ProjectDeletionRepository,
    ProjectDeletionService,
    ProjectImportService,
    ProjectQueryService,
    ProjectRepository,
    ProjectSource,
)
from backend.app.services.projects.deletion_journal import ProjectDeletionJournal
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths


class RecordingModel(ModelProvider):
    """记录真正发给模型的参数并请求原始源码，第二轮返回确定性测试文本。"""

    name = "test-provider"
    model = "fixed-test-model"
    answer = "## 结论\n候选需要验证。\n## 已核实问题\n未确认。\n## 未确认风险\n[view.js:3]。\n## 范围与局限\n只读测试范围。\n" + REPORT_END
    metadata = ModelResponseMetadata(finish_reason="stop", usage=ModelUsage(100, 40, 140))

    def __init__(self, calls: list[dict]) -> None:
        """保存共享审计列表，每个运行使用一个独立的模型替身实例。"""
        self.calls = calls
        self.turn = 0

    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        """工具预算耗尽时生成测试答案，不调用网络。"""
        return ModelResult(
            self.answer, self.name, self.model, self.metadata
        )

    async def generate_with_tools(
        self, *, instructions: str, prompt: str = "", tools: list[dict], messages=None, **kwargs
    ) -> ModelTurn:
        """第一轮读取项目原始文件，后续生成用于验证持久化的答案。"""
        self.calls.append(
            {"instructions": instructions, "prompt": (messages[0]["content"] if messages else prompt),
             "tools": tools, "messages": messages}
        )
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(
                "",
                self.name,
                self.model,
                (
                    ToolCall(
                        "read",
                        "read_file_range",
                        {"path": "view.js", "start_line": 1, "end_line": 8},
                    ),
                ),
                ModelResponseMetadata(finish_reason="tool_calls", usage=ModelUsage(100, 20, 120)),
            )
        return ModelTurn(
            self.answer, self.name, self.model, metadata=self.metadata,
        )


class SecurityExperimentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """所有资源位于临时工作区和临时 SQLite，禁止碰触真实项目/密钥。"""

    async def asyncSetUp(self) -> None:
        """创建隔离数据库、产物根目录、工作区和真实领域服务。"""
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.projects = ProjectRepository(self.sessions)
        self.store = AgentRunStore(self.sessions)
        self.artifact_patch = patch(
            "backend.app.services.projects.artifacts.ARTIFACT_ROOT", root / "artifacts"
        )
        self.artifact_patch.start()
        self.artifacts = ProjectArtifactRepository()
        self.paths = ProjectWorkspacePaths(root / "workspaces")
        self.workspace = ProjectWorkspaceService(paths=self.paths)
        self.activity = ProjectDeletionRepository(self.sessions)
        self.imports = ProjectImportService(
            project_repository=self.projects,
            artifact_repository=self.artifacts,
            workspace_service=self.workspace,
        )
        self.queries = ProjectQueryService(
            projects=self.projects,
            activity=self.activity,
            paths=self.paths,
            artifacts=self.artifacts,
        )
        self.repository = ExperimentRepository(self.sessions)
        self.comparisons = ExperimentComparisonService(
            repository=self.repository,
            projects=self.projects,
            run_store=self.store,
            artifacts=self.artifacts,
        )
        self.worker = AgentQueueWorker(
            worker_id="test-worker",
            store=self.store,
            projects=self.projects,
            artifacts=self.artifacts,
        )
        self.model_calls: list[dict] = []

    async def asyncTearDown(self) -> None:
        """仅释放本测试创建的临时资源，恢复全局路径配置。"""
        self.artifact_patch.stop()
        self.engine.dispose()
        self.temporary.cleanup()

    async def _import(self) -> str:
        """上传包含真实 JS DOM 数据流的 ZIP，通过正常项目生命周期生成证据。"""
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                "view.js",
                "export function render() {\n const v = location.hash;\n document.body.innerHTML = v;\n}\n",
            )
        stream.seek(0)
        result = await self.imports.import_project(
            AnalyzeProjectCommand(
                user_id="user1",
                source=ProjectSource.zip(stream, "fixture.zip"),
                max_workers=1,
            )
        )
        return result.project_id

    def _create(self, project_id: str) -> dict:
        """冻结假模型配置和执行顺序，确保测试能确定地验证真实入队顺序。"""
        with (
            patch(
                "backend.app.experiments.service.get_model_configuration",
                return_value=ModelConfiguration(
                    "test-provider", "fixed-test-model", "", True
                ),
            ),
            patch(
                "backend.app.experiments.service.random.shuffle",
                side_effect=lambda values: values.reverse(),
            ),
        ):
            return self.comparisons.create(
                project_id,
                "user1",
                ComparisonRequest(question="检查 HTML 注入风险", max_steps=2),
            )

    @staticmethod
    def _review() -> BlindReviewRequest:
        """两个通道评分相同也是合法实验结果；测试不预设实验组获胜。"""
        scores = LaneScores(
            correctness=3, completeness=3, evidence=3, hallucination_control=3
        )
        return BlindReviewRequest(preferred_lane="tie", left=scores, right=scores)

    async def test_full_functional_chain_and_experiment_feasibility(self) -> None:
        """验证真实导入、图、安全证据、队列、两组隔离、指标、恢复和依赖资源删除。"""
        project_id = await self._import()
        artifact = self.artifacts.load(project_id)
        self.assertTrue(artifact["dependency_graph"]["nodes"])
        self.assertTrue(
            any(
                item["dataflow_verified"]
                for item in artifact["security_evidence"]["candidates"]
            )
        )
        snapshot = await self.queries.snapshot(project_id, "user1")
        self.assertEqual(project_id, snapshot["project_id"])
        self.assertEqual(1, (await self.queries.list("user1"))["project_count"])
        view = self._create(project_id)
        record = self.repository.get(view["id"], "user1")
        self.assertEqual(["security_evidence", "baseline"], record.execution_order)
        with self.assertRaises(ExperimentError):
            self.comparisons.reveal(view["id"], "user1")
        with patch(
            "backend.app.agents.orchestrator.create_model_provider",
            side_effect=lambda model: RecordingModel(self.model_calls),
        ):
            self.assertTrue(await self.worker.run_once())
            self.assertEqual(
                "completed", self.store.get(record.graph_run_id, "user1").status
            )
            self.assertEqual(
                "queued", self.store.get(record.baseline_run_id, "user1").status
            )
            self.assertTrue(await self.worker.run_once())
        completed = self.comparisons.get(view["id"], "user1")
        self.assertTrue(completed["valid_for_review"])
        self.assertEqual("static-security-v2", completed["protocol"])
        first, second = self.model_calls[0], self.model_calls[2]
        self.assertEqual(SECURITY_EXPERIMENT_INSTRUCTIONS, first["instructions"])
        self.assertEqual(first["instructions"], second["instructions"])
        self.assertEqual(first["tools"], second["tools"])
        self.assertIn("STATIC_SECURITY_EVIDENCE", first["prompt"])
        envelope = json.loads(first["prompt"].split("STATIC_SECURITY_EVIDENCE\n", 1)[1])
        self.assertTrue(envelope["findings"])
        self.assertNotIn("STATIC_SECURITY_EVIDENCE", second["prompt"])
        for call in self.model_calls:
            for forbidden in (
                "DEPENDENCY_GRAPH_CONTEXT",
                "PROJECT_MANIFEST",
                "REPO_MAP",
                "uvicorn",
            ):
                self.assertNotIn(forbidden, call["prompt"])
        for run_id in (record.graph_run_id, record.baseline_run_id):
            metrics = collect_run_metrics(
                run_id, "user1", session_factory=self.sessions
            )
            self.assertEqual(1, metrics["tool_calls"])
            self.assertEqual("complete", metrics["usage_status"])
            self.assertEqual(200, metrics["actual_input_tokens"])
            self.assertEqual(60, metrics["actual_output_tokens"])
            self.assertEqual(["tool_calls", "stop"], metrics["finish_reasons"])
            self.assertGreater(
                metrics["request_characters"], metrics["context_characters"]
            )
            self.assertGreater(metrics["estimated_output_tokens"], 0)
            self.assertIsNotNone(metrics["duration_ms"])
            restored = AgentRunStore(self.sessions).snapshot(run_id, "user1")
            self.assertTrue(restored.evidence)
            self.assertEqual("completed", restored.run.status)
        self.assertEqual([], self.store.list_project_history(project_id, "user1"))
        review = self.comparisons.review(view["id"], "user1", self._review())
        self.assertEqual(
            {"baseline", "security_evidence"}, set(review["reveal"].values())
        )
        deletion = ProjectDeletionService(
            projects=self.projects,
            deletion_repository=self.activity,
            artifacts=self.artifacts,
            workspace=self.workspace,
            journal=ProjectDeletionJournal(
                Path(self.temporary.name, "deletion-journal")
            ),
        )
        await deletion.delete(project_id, "user1")
        self.assertIsNone(self.projects.get_owned(project_id, "user1"))
        self.assertIsNone(self.artifacts.load(project_id))
        self.assertIsNone(self.store.get(record.graph_run_id, "user1"))
        self.assertIsNone(self.repository.get(view["id"], "user1"))

    async def test_preflight_failure_creates_no_pair_or_queue_jobs(self) -> None:
        """任何一组完整输入无法构造时，不消费另一组模型资源。"""
        project_id = await self._import()
        with patch("backend.app.experiments.service.SecurityExperimentContextBuilder.build", side_effect=ValueError("budget")):
            with self.assertRaises(ExperimentError):
                self._create(project_id)
        with self.sessions() as session:
            self.assertEqual(0, session.query(AgentRunModel).count())
            self.assertEqual(0, session.query(AgentJobModel).count())
            self.assertEqual(0, session.query(ExperimentComparisonModel).count())

    async def test_incomplete_and_historical_answers_are_not_reviewable(self) -> None:
        """completed 状态不能掩盖截断；历史元数据未知时不回填为完整。"""
        project_id = await self._import()
        view = self._create(project_id)
        pair = self.repository.get(view["id"], "user1")
        for run_id in (pair.graph_run_id, pair.baseline_run_id):
            self.store.update(run_id, status="completed", answer=RecordingModel.answer)
        historical = self.comparisons.get(view["id"], "user1")
        self.assertFalse(historical["valid_for_review"])
        for run_id, status in ((pair.graph_run_id, "incomplete"), (pair.baseline_run_id, "complete")):
            self.store.add_event(run_id, "run.completed", {"answer_completeness": {"status": status}})
        incomplete = self.comparisons.get(view["id"], "user1")
        self.assertFalse(incomplete["valid_for_review"])
        with self.assertRaises(ExperimentError):
            self.comparisons.review(view["id"], "user1", self._review())

    async def test_partial_usage_and_mismatched_window_versions(self) -> None:
        """部分用量保持部分，工具窗口升级两侧不同不能复用为严格配对。"""
        project_id = await self._import()
        view = self._create(project_id)
        pair = self.repository.get(view["id"], "user1")
        for run_id, version in ((pair.graph_run_id, "complete-records-v2"), (pair.baseline_run_id, "legacy")):
            self.store.update(run_id, status="completed", answer=RecordingModel.answer)
            self.store.add_event(run_id, "run.started", {"observation_window_version": version})
            self.store.add_event(run_id, "model.started", {})
            self.store.add_event(run_id, "model.completed", {"metadata": {"usage": {"input_tokens": 10, "output_tokens": 0}}})
            self.store.add_event(run_id, "model.started", {})
            self.store.add_event(run_id, "model.completed", {"metadata": {}})
            self.store.add_event(run_id, "run.completed", {"answer_completeness": {"status": "complete"}})
        metrics = collect_run_metrics(pair.graph_run_id, "user1", session_factory=self.sessions)
        self.assertEqual("partial", metrics["usage_status"])
        self.assertEqual(10, metrics["actual_input_tokens"])
        self.assertEqual(0, metrics["actual_output_tokens"])
        self.assertIsNone(metrics["actual_total_tokens"])
        self.assertFalse(self.comparisons.get(view["id"], "user1")["valid_for_review"])

    async def test_pair_creation_is_atomic_and_failures_cannot_be_reviewed(
        self,
    ) -> None:
        """数据库插入失败不遗留孤儿任务；取消/失败不算有效负面实验结果。"""
        project_id = await self._import()
        view = self._create(project_id)
        record = self.repository.get(view["id"], "user1")
        duplicate = replace(
            record, baseline_run_id="new-baseline", graph_run_id="new-evidence"
        )
        with self.assertRaises(Exception):
            self.repository.create_pair(
                duplicate,
                AgentRunRequest(question=record.question, model="fixed-test-model"),
            )
        with self.sessions() as session:
            self.assertEqual(2, session.query(AgentRunModel).count())
            self.assertEqual(2, session.query(AgentJobModel).count())
            self.assertEqual(1, session.query(ExperimentComparisonModel).count())
        self.store.request_cancel(record.baseline_run_id, "user1")
        self.store.request_cancel(record.graph_run_id, "user1")
        completed = self.comparisons.get(view["id"], "user1")
        self.assertEqual("completed", completed["status"])
        self.assertFalse(completed["valid_for_review"])
        with self.assertRaises(ExperimentError):
            self.comparisons.review(view["id"], "user1", self._review())
        self.assertIsNone(self.store.get(record.baseline_run_id, "other-user"))

    async def test_http_permissions_and_terminal_sse(self) -> None:
        """通过实际 FastAPI 路由验证授权、终态快照 SSE 和盲评揭盲边界。"""
        from backend.app.api import experiment as experiment_api

        project_id = await self._import()
        view = self._create(project_id)
        record = self.repository.get(view["id"], "user1")
        self.store.request_cancel(record.baseline_run_id, "user1")
        self.store.request_cancel(record.graph_run_id, "user1")
        app = FastAPI()
        app.include_router(experiment_api.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "user1"}
        with patch.object(experiment_api, "comparison_service", self.comparisons):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/experiments/comparisons/{view['id']}/events"
                )
                self.assertEqual(200, response.status_code)
                self.assertIn("event: comparison.snapshot", response.text)
                self.assertIn('"valid_for_review": false', response.text)
                response = await client.get(
                    f"/api/experiments/comparisons/{view['id']}/reveal"
                )
                self.assertEqual(409, response.status_code)
                app.dependency_overrides[get_current_user] = lambda: {
                    "user_id": "other-user"
                }
                response = await client.get(
                    f"/api/experiments/comparisons/{view['id']}/events"
                )
                self.assertEqual(404, response.status_code)

    async def test_raw_file_tool_boundary_and_missing_provider(self) -> None:
        """两组共同工具拒绝越界路径，Provider 缺失不能静态回退并冒充试验成功。"""
        from backend.app.agents.tools.base import ToolContext
        from backend.app.experiments.tools import create_experiment_tool_registry

        project_id = await self._import()
        project = self.projects.get_owned(project_id, "user1")
        context = ToolContext(
            project_id=project_id,
            user_id="user1",
            project_root=Path(project.local_path),
            artifact={},
        )
        tools = create_experiment_tool_registry()
        listed = await tools.execute(
            "list_project_files", context, {"path": ".", "limit": 10}
        )
        self.assertIn("view.js", listed.content["files"])
        for name, arguments in (
            ("list_project_files", {"path": "..", "limit": 10}),
            (
                "read_file_range",
                {"path": "../outside.txt", "start_line": 1, "end_line": 10},
            ),
            ("get_dependency_neighbors", {}),
            ("get_project_manifest", {}),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                await tools.execute(name, context, arguments)
        view = self._create(project_id)
        with patch(
            "backend.app.agents.orchestrator.create_model_provider", return_value=None
        ):
            with self.assertLogs("backend.app.agents.orchestrator", level="ERROR"):
                self.assertTrue(await self.worker.run_once())
                self.assertTrue(await self.worker.run_once())
        self.assertFalse(self.comparisons.get(view["id"], "user1")["valid_for_review"])


if __name__ == "__main__":
    unittest.main()
