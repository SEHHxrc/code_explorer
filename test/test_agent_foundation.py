# -*- coding: utf-8 -*-
import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.context_builder import ProjectContextBuilder
from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.tools import create_project_tool_registry
from backend.app.agents.tools.base import ToolContext
from backend.app.llm.http import ModelEndpointError
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.models import Base


def sample_artifact():
    return {
        "manifest": {
            "project_name": "demo",
            "languages": ["python"],
            "frameworks": ["FastAPI"],
            "entrypoints": [{
                "kind": "application", "name": "app", "path": "main.py", "line": 1,
                "command": "uvicorn main:app", "confidence": 1.0,
            }],
        },
        "repo_map": "PROJECT demo\nENTRYPOINTS:\n- application app @ main.py:1\nIMPORTANT SYMBOLS:\n- function run @ main.py:4",
        "overview": "# demo\n\n入口位于 `main.py:1`。",
        "file_symbols": {
            "main.py": [{
                "name": "run", "kind": "function", "fully_qualified_name": "run",
                "extent_utf16": {"start": {"line_number": 4}},
            }],
        },
        "dependency_graph": {
            "nodes": [
                {"id": "main.py", "name": "main.py", "kind": "module", "file": "main.py", "line": 1},
                {"id": "run", "name": "run", "kind": "function", "file": "main.py", "line": 4},
            ],
            "links": [{"source": "main.py", "target": "run", "relation": "contains"}],
        },
    }


class MemoryRunStore:
    def __init__(self):
        self.status = "queued"
        self.values = {}
        self.events = []

    def update(self, run_id, **values):
        self.values.update(values)
        self.status = values.get("status", self.status)

    def add_event(self, run_id, event_type, payload):
        self.events.append((event_type, payload))


class AgentFoundationTests(unittest.TestCase):
    def test_run_store_persists_status_and_ordered_events(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        test_session = sessionmaker(bind=engine)
        with patch("backend.app.agents.run_store.SessionLocal", test_session):
            store = AgentRunStore()
            store.create(
                run_id="r1", project_id="p1", user_id="u1",
                request=AgentRunRequest(question="入口在哪", use_model=False),
            )
            store.add_event("r1", "run.started", {})
            store.add_event("r1", "run.completed", {"answer": "ok"})
            store.update("r1", status="completed", answer="ok")
            self.assertEqual(store.get("r1", "u1").status, "completed")
            self.assertEqual([event.sequence for event in store.events_after("r1", 0)], [1, 2])

    def test_project_history_restores_events_and_evidence_without_experiment_runs(self):
        """历史列表仅展示普通运行，快照可安全重建时间线和去重证据。"""
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        store = AgentRunStore(sessions)
        request = AgentRunRequest(question="检查项目安全问题", use_model=True)
        store.create(run_id="normal", project_id="p1", user_id="u1", request=request)
        store.create(
            run_id="experiment", project_id="p1", user_id="u1",
            request=request, strategy="graph",
        )
        evidence = {"path": "main.py", "line": 7, "symbol": "run", "detail": "source excerpt"}
        store.add_event("normal", "run.started", {"project_id": "p1"})
        store.add_event("normal", "model.delta", {"delta": "不应进入快照"})
        store.add_event("normal", "tool.requested", {
            "call_id": "c1", "name": "read_file_range", "arguments": {"path": "main.py"},
        })
        store.add_event("normal", "tool.completed", {
            "call_id": "c1", "name": "read_file_range",
            "result": {"content": "源码正文不应返回", "evidence": [evidence]},
        })
        store.add_event("normal", "run.completed", {
            "answer": "完成", "provider": "compatible", "model": "demo", "evidence": [evidence],
        })
        store.update(
            "normal", status="completed", answer="完成", provider="compatible", model="demo",
        )

        history = store.list_project_history("p1", "u1")
        self.assertEqual([item.id for item in history], ["normal"])
        self.assertEqual(history[0].tool_calls, 1)
        self.assertEqual(history[0].evidence_count, 1)
        snapshot = store.snapshot("normal", "u1")
        self.assertEqual(snapshot.run.answer, "完成")
        self.assertEqual(len(snapshot.evidence), 1)
        self.assertNotIn("model.delta", [event.type for event in snapshot.events])
        completed = next(event for event in snapshot.events if event.type == "tool.completed")
        self.assertNotIn("result", completed.payload)
        self.assertIsNotNone(snapshot.events[0].created_at)

    def test_context_is_bounded_and_includes_entrypoint_evidence(self):
        packet = ProjectContextBuilder().build(
            project_id="p1", question="FastAPI 入口在哪里", artifact=sample_artifact(),
        )
        self.assertIn("PROJECT_MANIFEST", packet.prompt_context)
        self.assertLessEqual(len(packet.prompt_context), 18000)
        self.assertEqual(packet.evidence[0].path, "main.py")

    def test_tool_schemas_are_strict_and_require_all_properties(self):
        schemas = create_project_tool_registry().schemas()
        search = next(item for item in schemas if item["name"] == "search_symbols")
        self.assertTrue(search["strict"])
        self.assertFalse(search["parameters"]["additionalProperties"])
        self.assertEqual(set(search["parameters"]["required"]), {"query", "limit"})

    def test_read_tool_blocks_traversal_and_redacts_secrets(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "main.py").write_text("API_KEY=super-secret\nprint('ok')\n", encoding="utf-8")
                registry = create_project_tool_registry()
                context = ToolContext("p1", "u1", root, sample_artifact())
                result = await registry.execute("read_file_range", context, {
                    "path": "main.py", "start_line": 1, "end_line": 20,
                })
                self.assertIn("[REDACTED]", result.content["text"])
                with self.assertRaises(ValueError):
                    await registry.execute("read_file_range", context, {
                        "path": "../outside.py", "start_line": 1, "end_line": 2,
                    })
        asyncio.run(scenario())

    def test_symbol_and_dependency_tools_return_evidence(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                context = ToolContext("p1", "u1", Path(directory), sample_artifact())
                registry = create_project_tool_registry()
                symbols = await registry.execute("search_symbols", context, {"query": "run", "limit": 5})
                neighbors = await registry.execute("get_dependency_neighbors", context, {
                    "node_id": "main.py", "direction": "outgoing", "limit": 5,
                })
                self.assertEqual(symbols.content[0]["line"], 4)
                self.assertEqual(neighbors.content[0]["node_id"], "run")
                self.assertTrue(neighbors.evidence)
        asyncio.run(scenario())

    def test_static_agent_run_completes_with_events(self):
        async def scenario():
            store = MemoryRunStore()
            manager = AgentRunManager(store=store)
            with tempfile.TemporaryDirectory() as directory:
                await manager._run(
                    run_id="r1", project_id="p1", user_id="u1", project_root=directory,
                    artifact=sample_artifact(),
                    request=AgentRunRequest(question="入口在哪", use_model=False),
                )
            self.assertEqual(store.status, "completed")
            event_types = [item[0] for item in store.events]
            self.assertIn("context.ready", event_types)
            self.assertIn("run.completed", event_types)
        asyncio.run(scenario())

    def test_model_failure_emits_safe_actionable_diagnostics(self):
        """模型额度错误应进入运行事件，但不得暴露上游原始正文。"""
        async def scenario():
            store = MemoryRunStore()
            manager = AgentRunManager(store=store)
            failure = ModelEndpointError(
                status_code=429,
                error_type="insufficient_quota",
                error_code="credit_balance_exhausted",
                request_id="req_test",
            )
            provider = SimpleNamespace(
                name="openai",
                model="gpt-test",
                generate_with_tools=AsyncMock(side_effect=failure),
            )
            with tempfile.TemporaryDirectory() as directory, patch(
                "backend.app.agents.orchestrator.create_model_provider",
                return_value=provider,
            ) as provider_factory:
                await manager._run(
                    run_id="r1", project_id="p1", user_id="u1", project_root=directory,
                    artifact=sample_artifact(),
                    request=AgentRunRequest(
                        question="测试模型",
                        use_model=True,
                        model="gpt-selected",
                    ),
                )
            provider_factory.assert_called_once_with("gpt-selected")
            self.assertEqual(store.status, "failed")
            failed = next(payload for event, payload in store.events if event == "run.failed")
            started = next(payload for event, payload in store.events if event == "model.started")
            self.assertEqual(failed["error_code"], "credit_balance_exhausted")
            self.assertEqual(failed["request_id"], "req_test")
            self.assertIn("余额不足", failed["error"])
            self.assertGreater(started["request_chars"], 0)
            self.assertGreater(started["tool_count"], 0)
            self.assertNotIn("prompt", started)

        asyncio.run(scenario())

    def test_openai_and_compatible_tool_call_parsing(self):
        async def scenario():
            with patch(
                "backend.app.llm.providers.openai_provider.post_json",
                new=AsyncMock(return_value={"output": [{
                    "type": "function_call", "call_id": "c1", "name": "search_symbols",
                    "arguments": '{"query":"app"}',
                }]}),
            ) as openai_request:
                turn = await OpenAIResponsesProvider(
                    api_key="key", model="model", max_output_tokens=321,
                ).generate_with_tools(instructions="i", prompt="p", tools=[])
                self.assertEqual(turn.tool_calls[0].arguments, {"query": "app"})
                self.assertEqual(openai_request.await_args.args[1]["max_output_tokens"], 321)
            with patch(
                "backend.app.llm.providers.compatible_provider.post_json",
                new=AsyncMock(return_value={"choices": [{"message": {"tool_calls": [{
                    "id": "c2", "function": {"name": "list_entrypoints", "arguments": "{}"},
                }]}}]}),
            ) as compatible_request:
                turn = await OpenAICompatibleProvider(
                    provider_name="ollama", base_url="http://localhost", model="model",
                    max_output_tokens=654,
                ).generate_with_tools(instructions="i", prompt="p", tools=[])
                self.assertEqual(turn.tool_calls[0].name, "list_entrypoints")
                self.assertEqual(compatible_request.await_args.args[1]["max_tokens"], 654)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
