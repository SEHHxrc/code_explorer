"""解析器资源与最终报告协议回归；不读取真实项目记录，不发送模型请求。"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.llm.base import ModelTurn, ToolCall
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm.conversation import ModelMessage
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.experiments.contracts import ComparisonRequest, ExperimentError
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.project_analysis.pipeline import ProjectAnalysisPipeline, ProjectAnalysisPrerequisiteError
from backend.app.services.syntax_analysis import TreeSitterParserPool
from backend.app.services.syntax_analysis.parser_runtime import ParserUnavailableError, parser_cache_directory
from backend.app.services.projects import AnalyzeProjectCommand, ProjectImportService, ProjectSource, ProjectAnalysisError
from backend.app.services.projects.progress import ImportProgressStore
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths
from test.test_agent_foundation import MemoryRunStore, sample_artifact
from test.test_project_analysis_service import RecordingArtifacts, RecordingProjects, project_zip


class ParserRuntimeTests(unittest.TestCase):
    """离线扫描不能因资源缺失触发隐式下载或产生空的成功分析。"""

    def test_missing_parser_never_enters_package_download(self) -> None:
        with patch("backend.app.services.syntax_analysis.parser_runtime.downloaded_languages", return_value=[]), \
             patch("backend.app.services.syntax_analysis.tree_parser.get_parser") as load:
            with self.assertRaises(ParserUnavailableError):
                TreeSitterParserPool().get("python")
        load.assert_not_called()

    def test_analyzer_checks_resources_before_parsing_any_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").write_text("def run():\n return 1\n", encoding="utf-8")
            with patch("backend.app.services.syntax_analysis.parser_runtime.downloaded_languages", return_value=[]), \
                 patch.object(UnifiedCodeAnalyzer, "_analyze_file") as parse:
                with self.assertRaises(ProjectAnalysisPrerequisiteError) as failure:
                    ProjectAnalysisPipeline().analyze(directory)
        self.assertIn("本地语法解析器", failure.exception.public_message)
        self.assertNotIn(directory, failure.exception.public_message)
        parse.assert_not_called()

    def test_broken_language_is_loaded_only_once(self) -> None:
        pool = TreeSitterParserPool()
        with patch("backend.app.services.syntax_analysis.tree_parser.require_local_grammars"), \
             patch("backend.app.services.syntax_analysis.tree_parser.get_parser", side_effect=RuntimeError("broken")) as load:
            for _ in range(3):
                with self.assertRaises(ParserUnavailableError):
                    pool.get("python")
        self.assertEqual(1, load.call_count)

    def test_missing_parser_rolls_back_import_and_reports_failed_progress(self) -> None:
        progress = ImportProgressStore()
        request_id = progress.create("user")
        tracker = progress.claim(request_id, "user")
        artifacts, projects = RecordingArtifacts(), RecordingProjects()
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectWorkspacePaths(Path(directory) / "users")
            service = ProjectImportService(
                artifact_repository=artifacts, project_repository=projects,
                workspace_service=ProjectWorkspaceService(paths=paths),
            )
            with patch("backend.app.services.syntax_analysis.parser_runtime.downloaded_languages", return_value=[]):
                with self.assertRaises(ProjectAnalysisError) as failure:
                    asyncio.run(service.import_project(
                        AnalyzeProjectCommand(user_id="user", source=ProjectSource.zip(project_zip(), "sample.zip")),
                        progress=tracker,
                    ))
            self.assertEqual(503, failure.exception.status_code)
            self.assertFalse(any((Path(directory) / "users" / "user" / ".staging").glob("*")))
        self.assertIsNone(artifacts.saved)
        self.assertIsNone(projects.created)
        snapshot = progress.snapshot(request_id, "user")
        self.assertEqual("failed", snapshot["status"])
        self.assertIn("本地语法解析器", snapshot["message"])

    def test_default_cache_is_not_affected_by_user_profile(self) -> None:
        with patch.dict(os.environ, {"CODE_EXPLORER_PARSER_CACHE_DIR": "", "LOCALAPPDATA": "profile-one"}):
            first = parser_cache_directory()
        with patch.dict(os.environ, {"CODE_EXPLORER_PARSER_CACHE_DIR": "", "LOCALAPPDATA": "profile-two"}):
            self.assertEqual(first, parser_cache_directory())
        self.assertIn("runtime", first.parts)


class FinalAnswerProtocolTests(unittest.TestCase):
    """两组共用最终工具禁用策略；违反协议时不能执行额外工具或通过盲评。"""

    def test_empty_analysis_from_old_parser_failure_cannot_start_new_pair(self) -> None:
        repository, projects, artifacts, runs = Mock(), Mock(), Mock(), Mock()
        artifact = {"security_evidence": {"completed": True}, "analysis_statistics": {"parse_failures": 6},
                    "analysis_diagnostics": {"parse_failures": [{"file": "main.py"}]}}
        artifacts.load.return_value = artifact
        service = ExperimentComparisonService(repository=repository, projects=projects, artifacts=artifacts, run_store=runs)
        with patch("backend.app.experiments.service.get_model_configuration", return_value=SimpleNamespace(configured=True)):
            with self.assertRaises(ExperimentError) as failure:
                service.create("project", "user", ComparisonRequest(question="q"))
        self.assertIn("解析全部失败", str(failure.exception))
        self.assertEqual({"completed": True}, artifact["security_evidence"])
        repository.create_pair.assert_not_called()

    def test_both_adapters_send_explicit_none_with_tool_schemas(self) -> None:
        tools = [{"name": "read", "parameters": {"type": "object"}}]
        providers = (
            OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock"),
            OpenAIResponsesProvider(api_key="fake", model="mock"),
        )
        for provider in providers:
            payload = provider.request_payload(
                instructions="final", messages=[ModelMessage(role="user", content="q")],
                tools=tools, tool_choice="none",
            )
            self.assertEqual("none", payload["tool_choice"])
            self.assertTrue(payload["tools"])

    def _run_final(self, final: ModelTurn) -> tuple[MemoryRunStore, SimpleNamespace]:
        store = MemoryRunStore()
        first = ModelTurn(text="", provider="compatible", model="mock", tool_calls=(ToolCall("first", "list_entrypoints"),))
        provider = SimpleNamespace(name="compatible", model="mock", generate_with_tools=AsyncMock(side_effect=[first, final]))
        manager = AgentRunManager(store=store)
        with tempfile.TemporaryDirectory() as directory, \
             patch("backend.app.agents.orchestrator.create_model_provider", return_value=provider):
            asyncio.run(manager._run(
                run_id="run", project_id="project", user_id="user", project_root=directory,
                artifact=sample_artifact(), request=AgentRunRequest(question="q", max_steps=1),
            ))
        return store, provider

    def test_step_limit_requests_answer_with_none_and_complete_history(self) -> None:
        final = ModelTurn(text="回答", provider="compatible", model="mock", metadata=ModelResponseMetadata(finish_reason="stop"))
        store, provider = self._run_final(final)
        self.assertEqual("completed", store.status)
        request = provider.generate_with_tools.call_args.kwargs
        self.assertEqual("none", request["tool_choice"])
        self.assertTrue(request["tools"])
        self.assertEqual("first", request["messages"][-2]["tool_call_id"])
        self.assertIn("最终报告阶段", request["messages"][-1]["content"])

    def test_final_tool_call_is_not_executed_and_has_actionable_error(self) -> None:
        final = ModelTurn(text="", provider="compatible", model="mock", tool_calls=(ToolCall("forbidden", "list_entrypoints"),),
                          metadata=ModelResponseMetadata(finish_reason="tool_calls", usage=ModelUsage(10, 3, 13)))
        store, _ = self._run_final(final)
        self.assertEqual("failed", store.status)
        failed = next(payload for kind, payload in store.events if kind == "run.failed")
        self.assertEqual("unexpected_tool_calls", failed["error_code"])
        self.assertEqual(13, failed["final_response_metadata"]["usage"]["total_tokens"])
        self.assertEqual("incomplete", failed["answer_completeness"]["status"])
        requested = [payload for kind, payload in store.events if kind == "tool.requested"]
        self.assertEqual(["first"], [payload["call_id"] for payload in requested])

    def test_empty_output_budget_and_refusal_are_distinct(self) -> None:
        for metadata, code in (
            (ModelResponseMetadata(finish_reason="length"), "empty_incomplete_output"),
            (ModelResponseMetadata(finish_reason="stop", refused=True), "model_refused"),
            (ModelResponseMetadata(finish_reason="stop"), "empty_model_answer"),
        ):
            with self.subTest(code=code):
                store, _ = self._run_final(ModelTurn(text="", provider="compatible", model="mock", metadata=metadata))
                failed = next(payload for kind, payload in store.events if kind == "run.failed")
                self.assertEqual(code, failed["error_code"])


if __name__ == "__main__":
    unittest.main()
