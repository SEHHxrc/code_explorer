"""迁移后模型接入与预算回归；仅模拟 HTTP 和内存数据库，不读取实验记录。"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.llm.budget import BudgetPlanner, ModelBudgetError
from backend.app.llm.http import _request_json_sync
from backend.app.llm.response_metadata import response_metadata
from backend.app.llm.settings import ModelLimits, get_model_limits
from backend.app.llm.tokens import TokenCounter
from backend.app.models import Base


class ModelAlignmentTests(unittest.TestCase):
    """窗口与估算不是平台实际容量；费用字段不能由估算回填。"""

    def test_large_declared_window_does_not_have_an_implicit_5500_char_limit(self) -> None:
        """新配置允许超过旧字符阈值，同时明确保留最大输出和模板余量。"""
        environment = {
            "CODE_EXPLORER_LLM_MODEL": "FW-GLM-5.3-noklok-1",
            "CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS": "0",
            "CODE_EXPLORER_LLM_MAX_INPUT_TOKENS": "524288",
            "CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS": "524288",
            "CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": "8192",
        }
        with patch.dict(os.environ, environment, clear=True):
            limits = get_model_limits()
        planner = BudgetPlanner(limits)
        payload = {"messages": [{"role": "user", "content": "中文" * 6000}]}
        self.assertEqual(515584, planner.input_limit)
        self.assertTrue(planner.fits(payload))
        self.assertEqual("operator_configured", planner.require(payload)["capacity_source"])
        with self.assertRaises(ModelBudgetError):
            BudgetPlanner(replace(limits, max_context_chars=5500)).require(payload)
        with self.assertRaises(ModelBudgetError):
            planner.require({"content": "x" * 515584})

    def test_window_declaration_alone_does_not_raise_application_budget(self) -> None:
        """防止把模型窗口配置误当成应用预算，默认行为仍受独立预算约束。"""
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS": "524288"}, clear=True):
            self.assertEqual(17488, BudgetPlanner(get_model_limits()).input_limit)

    def test_budget_validation_counts_a_request_only_once(self) -> None:
        """完整请求检查只序列化/估算一次，避免大上下文的重复开销。"""
        counter = TokenCounter()
        planner = BudgetPlanner(ModelLimits(0, 2400), counter=counter)
        with patch.object(counter, "request", wraps=counter.request) as measure:
            planner.require({"messages": [{"role": "user", "content": "输入"}]})
        self.assertEqual(1, measure.call_count)

    def test_wire_json_matches_estimate_and_does_not_count_credentials(self) -> None:
        """模拟发送检查紧凑 JSON 字节数与估算一致，认证头不进入 Token 估算。"""
        payload = {"messages": [{"role": "user", "content": "中文🙂"}], "tools": []}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true}'
        with patch("backend.app.llm.http.urlopen", return_value=response) as send:
            self.assertEqual({"ok": True}, _request_json_sync(
                "POST", "http://mock/chat/completions", payload, {"Authorization": "Bearer fake"}, 1,
            ))
        request = send.call_args.args[0]
        self.assertEqual(len(request.data), TokenCounter().request(payload).tokens)
        self.assertEqual(payload, json.loads(request.data))
        self.assertNotIn(b"fake", request.data)

    def test_usage_aliases_keep_real_zero_and_missing_values(self) -> None:
        """兼容端点的两种真实字段名均可识别；零、未知及非法数据分别处理。"""
        metadata = response_metadata({"usage": {
            "input_tokens": 10, "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0},
        }}, responses_api=False)
        self.assertEqual(10, metadata.usage.input_tokens)
        self.assertEqual(0, metadata.usage.output_tokens)
        self.assertEqual(0, metadata.usage.reasoning_tokens)
        self.assertIsNone(metadata.usage.total_tokens)
        native = response_metadata({"usage": {"prompt_tokens": 0, "input_tokens": 100}}, responses_api=False)
        self.assertEqual(0, native.usage.input_tokens)
        for usage in (None, "invalid", {"prompt_tokens": True, "completion_tokens": "10"}):
            result = response_metadata({"usage": usage}, responses_api=False)
            self.assertIsNone(result.usage.input_tokens)
            self.assertIsNone(result.usage.output_tokens)


class FailedRunMetricsTests(unittest.TestCase):
    """未完成运行仍保留已收集证据与供应商用量，不改写事件或假装回答完整。"""

    def setUp(self) -> None:
        """创建与真实项目数据库完全隔离的内存事件存储。"""
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.store = AgentRunStore(self.sessions)
        self.store.create(run_id="run", project_id="project", user_id="user", request=AgentRunRequest(question="q"))

    def tearDown(self) -> None:
        """释放本用例的内存数据库连接。"""
        self.engine.dispose()

    def test_failed_run_metrics_include_deduplicated_evidence_and_partial_usage(self) -> None:
        """第二轮失败不抹掉第一轮 usage 和工具证据，也不把未知消耗记为零。"""
        first = {"path": "main.py", "line": 1, "symbol": "entry"}
        second = {"path": "main.py", "line": 5, "symbol": "sink"}
        for kind, payload in (
            ("context.ready", {"evidence": [first]}),
            ("model.started", {}),
            ("model.completed", {"response_chars": 2, "response_utf8_bytes": 6,
                                 "metadata": {"finish_reason": "tool_calls", "usage": {
                                     "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}}),
            ("tool.completed", {"evidence": [second], "result": {"evidence": [first, second]}}),
            ("model.started", {}),
            ("run.failed", {"error": "failed"}),
        ):
            self.store.add_event("run", kind, payload)
        self.store.update("run", status="failed", error="failed")
        before = [event.model_dump() for event in self.store.events_after("run", 0)]
        metrics = collect_run_metrics("run", "user", session_factory=self.sessions)
        self.assertEqual(2, metrics["evidence_count"])
        self.assertEqual(2, metrics["tool_evidence_count"])
        self.assertEqual("partial", metrics["usage_status"])
        self.assertEqual(15, metrics["actual_total_tokens"])
        self.assertEqual(6, metrics["estimated_output_tokens"])
        self.assertEqual("utf8_bytes_estimate", metrics["output_estimation_method"])
        self.assertEqual("unknown", metrics["answer_completeness"]["status"])
        self.assertEqual(before, [event.model_dump() for event in self.store.events_after("run", 0)])

    def test_legacy_output_estimate_is_explicit_and_does_not_fill_usage(self) -> None:
        """旧事件只提供历史近似标签，不修改原记录或补造真实供应商消耗。"""
        self.store.add_event("run", "model.completed", {"response_chars": 9})
        metrics = collect_run_metrics("run", "user", session_factory=self.sessions)
        self.assertEqual(3, metrics["estimated_output_tokens"])
        self.assertEqual("legacy_characters_divided_by_four", metrics["output_estimation_method"])
        self.assertIsNone(metrics["actual_output_tokens"])
        self.assertEqual("unavailable", metrics["usage_status"])


if __name__ == "__main__":
    unittest.main()
