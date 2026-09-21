# -*- coding: utf-8 -*-
"""模型 HTTP 错误、重试和显式连通性诊断测试。"""

import asyncio
from email.message import Message
from io import BytesIO
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from backend.app.llm.diagnostics import list_available_models, probe_model_connection
from backend.app.llm.http import (
    ModelConnectionError,
    ModelEndpointError,
    _post_json_sync,
    post_json,
)


class ModelHttpErrorTests(unittest.TestCase):
    """验证上游错误只保留安全诊断字段。"""

    def test_extracts_quota_code_without_exposing_provider_message(self) -> None:
        """余额型 429 应可诊断、不可重试且不泄露原始消息。"""
        headers = Message()
        headers["x-request-id"] = "req_safe123"
        body = BytesIO(json.dumps({
            "error": {
                "message": "sensitive account detail",
                "type": "insufficient_quota",
                "code": "credit_balance_exhausted",
            },
        }).encode("utf-8"))
        error = HTTPError("https://example.test", 429, "Too Many Requests", headers, body)
        with patch("backend.app.llm.http.urlopen", side_effect=error):
            with self.assertRaises(ModelEndpointError) as raised:
                _post_json_sync("https://example.test", {}, {}, 1.0)
        exception = raised.exception
        self.assertEqual(exception.error_code, "credit_balance_exhausted")
        self.assertEqual(exception.request_id, "req_safe123")
        self.assertFalse(exception.retryable)
        self.assertNotIn("sensitive account detail", exception.public_message)

    def test_retries_temporary_rate_limit_only(self) -> None:
        """临时限流或网关故障重试后成功，消费额度错误立即返回。"""
        temporary = ModelEndpointError(
            status_code=429,
            error_type="rate_limit_error",
            error_code="slow_down",
            retry_after="0",
        )
        with patch(
            "backend.app.llm.http._request_json_sync",
            side_effect=[temporary, {"ok": True}],
        ) as request:
            result = asyncio.run(post_json("https://example.test", {}, {}, 1.0))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)

        interrupted = ModelConnectionError("connection reset")
        with patch(
            "backend.app.llm.http._request_json_sync",
            side_effect=[interrupted, {"ok": True}],
        ) as request:
            with patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()):
                result = asyncio.run(post_json("https://example.test", {}, {}, 1.0))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)

        quota = ModelEndpointError(status_code=429, error_code="project_spend_limit_exceeded")
        with patch("backend.app.llm.http._request_json_sync", side_effect=quota) as request:
            with self.assertRaises(ModelEndpointError):
                asyncio.run(post_json("https://example.test", {}, {}, 1.0))
        self.assertEqual(request.call_count, 1)

        unavailable = ModelEndpointError(status_code=503)
        with patch(
            "backend.app.llm.http._request_json_sync",
            side_effect=[unavailable, {"ok": True}],
        ) as request:
            with patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()):
                result = asyncio.run(post_json("https://example.test", {}, {}, 1.0))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)


class ModelDiagnosticsTests(unittest.TestCase):
    """验证探测和模型目录不会暴露密钥且能返回结构化状态。"""

    environment = {
        "CODE_EXPLORER_LLM_PROVIDER": "openai",
        "CODE_EXPLORER_LLM_MODEL": "gpt-test",
        "CODE_EXPLORER_LLM_API_KEY": "'secret-key'",
        "CODE_EXPLORER_LLM_BASE_URL": "https://api.example.test/v1",
    }

    def test_probe_reports_success(self) -> None:
        """最小生成成功时应同时确认连接、模型与生成能力。"""
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.diagnostics.post_json",
            new=AsyncMock(return_value={
                "output": [{"type": "function_call", "name": "agent_compatibility_probe"}],
            }),
        ) as request:
            result = asyncio.run(probe_model_connection())
        self.assertTrue(result["connected"])
        self.assertTrue(result["model_accessible"])
        self.assertTrue(result["generation_available"])
        self.assertTrue(result["agent_compatible"])
        self.assertEqual(request.await_args.args[1]["tool_choice"], "required")
        self.assertEqual(request.await_args.args[2]["Authorization"], "Bearer secret-key")
        self.assertNotIn("secret-key", json.dumps(result))

    def test_probe_uses_requested_model_override(self) -> None:
        """智能体页面选择的模型应进入探测请求并出现在安全结果中。"""
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.diagnostics.post_json",
            new=AsyncMock(return_value={
                "output": [{"type": "function_call", "name": "agent_compatibility_probe"}],
            }),
        ) as request:
            result = asyncio.run(probe_model_connection("gpt-selected"))
        self.assertEqual(request.await_args.args[1]["model"], "gpt-selected")
        self.assertEqual(result["model"], "gpt-selected")

    def test_probe_preserves_safe_quota_diagnosis(self) -> None:
        """余额错误应返回错误码和用户提示，不自动重试或抛出 500。"""
        failure = ModelEndpointError(
            status_code=429,
            error_type="insufficient_quota",
            error_code="credit_balance_exhausted",
            request_id="req_123",
        )
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.diagnostics.post_json",
            new=AsyncMock(side_effect=failure),
        ):
            result = asyncio.run(probe_model_connection())
        self.assertTrue(result["connected"])
        self.assertFalse(result["generation_available"])
        self.assertEqual(result["error_code"], "credit_balance_exhausted")
        self.assertEqual(result["request_id"], "req_123")
        self.assertFalse(result["retryable"])

    def test_probe_warns_when_model_does_not_call_tool(self) -> None:
        """端点接受工具字段但模型不调用工具时，不应误报 Agent 完全可用。"""
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.diagnostics.post_json",
            new=AsyncMock(return_value={"output_text": "OK", "output": []}),
        ):
            result = asyncio.run(probe_model_connection())
        self.assertTrue(result["generation_available"])
        self.assertFalse(result["agent_compatible"])

    def test_compatible_probe_uses_chat_completion_tool_shape(self) -> None:
        """兼容供应商应按 Chat Completions 结构发送并识别函数工具调用。"""
        environment = {
            **self.environment,
            "CODE_EXPLORER_LLM_PROVIDER": "compatible",
        }
        response = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {"name": "agent_compatibility_probe", "arguments": "{}"},
                    }],
                },
            }],
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "backend.app.llm.diagnostics.post_json",
            new=AsyncMock(return_value=response),
        ) as request:
            result = asyncio.run(probe_model_connection())
        payload = request.await_args.args[1]
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "agent_compatibility_probe")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertTrue(result["agent_compatible"])

    def test_lists_sorted_visible_models(self) -> None:
        """模型目录只返回去重排序后的模型 ID。"""
        payload = {"data": [{"id": "gpt-z"}, {"id": "gpt-a"}, {"id": "gpt-a"}]}
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.diagnostics.get_json",
            new=AsyncMock(return_value=payload),
        ):
            result = asyncio.run(list_available_models())
        self.assertEqual(result["models"], ["gpt-a", "gpt-z"])
        self.assertTrue(result["connected"])


if __name__ == "__main__":
    unittest.main()
