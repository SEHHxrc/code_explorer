# -*- coding: utf-8 -*-
"""模型 HTTP 错误、重试和显式连通性诊断测试。"""

import asyncio
from email.message import Message
from email.utils import formatdate
from io import BytesIO
from http.client import IncompleteRead
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError, URLError

from backend.app.llm.diagnostics import list_available_models, probe_model_connection
from backend.app.llm.http import (
    ModelConnectionError,
    ModelEndpointError,
    _request_json_sync,
    post_json,
    _retry_delay,
)
from backend.app.llm.observation import observe_requests


class ModelHttpErrorTests(unittest.TestCase):
    """验证上游错误只保留安全诊断字段。"""

    def test_rate_limit_reached_retries_same_request_with_bounded_waits(self) -> None:
        """识别实际端点的速率码，最多三次尝试且正文不变，等待不计成请求。"""
        error = ModelEndpointError(status_code=429, error_code="RateLimitReached")
        payload = {"model": "mock", "max_tokens": 32768, "messages": []}
        observer = Mock()
        with patch("backend.app.llm.http._request_json_sync", side_effect=[error, error, {"ok": True}]) as send, \
                patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()) as sleep, \
                patch("backend.app.llm.http.random.uniform", return_value=0), observe_requests(observer):
            self.assertEqual({"ok": True}, asyncio.run(post_json("http://mock", payload, {}, 180)))
        self.assertEqual([30, 60], [call.args[0] for call in sleep.call_args_list])
        self.assertEqual([1, 2, 3], [call.kwargs["attempt"] for call in send.call_args_list])
        self.assertTrue(all(call.args[2] is payload for call in send.call_args_list))
        self.assertEqual(2, observer.retry_wait.call_count)
        self.assertTrue(all(call.kwargs["completed"] for call in observer.retry_wait.call_args_list))

    def test_retry_limits_zero_override_and_quota_precedence(self) -> None:
        """不识别的 429 不猜测为临时错误；余额/权限/超窗/零重试均不等待。"""
        failures = [ModelEndpointError(status_code=429, error_code="unknown_limit"),
                    ModelEndpointError(status_code=429, error_code="insufficient_quota"),
                    ModelEndpointError(status_code=429, error_code="credit_balance_exhausted"),
                    ModelEndpointError(status_code=503, error_type="insufficient_quota"),
                    ModelEndpointError(status_code=429, error_code="context_length_exceeded"),
                    ModelEndpointError(status_code=403, error_code="RateLimitReached")]
        for failure in failures:
            self.assertFalse(failure.retryable)
        limited = ModelEndpointError(status_code=429, error_code="RateLimitReached")
        with patch("backend.app.llm.http._request_json_sync", side_effect=limited) as send, \
                patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(ModelEndpointError):
                asyncio.run(post_json("http://mock", {}, {}, 180, transient_retries=0))
            sleep.assert_not_called()
            self.assertEqual(1, send.call_count)
        with patch("backend.app.llm.http._request_json_sync", side_effect=limited) as send, \
                patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(ModelEndpointError):
                asyncio.run(post_json("http://mock", {}, {}, 180, transient_retries=100))
            self.assertEqual(3, send.call_count)
            self.assertEqual(2, sleep.call_count)

    def test_retry_after_seconds_date_invalid_and_excessive_delay(self) -> None:
        """429 遵守服务端等待；长于本地边界时停止，不提前重试。"""
        with patch("backend.app.llm.http.time.time", return_value=1000), \
                patch("backend.app.llm.http.random.uniform", return_value=0):
            self.assertEqual(45, _retry_delay("45", 0, rate_limited=True))
            self.assertEqual(20, _retry_delay(formatdate(1020, usegmt=True), 0, rate_limited=True))
            self.assertEqual(0, _retry_delay(formatdate(900, usegmt=True), 0, rate_limited=True))
            for invalid in (None, "invalid", "nan", "inf"):
                self.assertEqual(30, _retry_delay(invalid, 0, rate_limited=True))
            self.assertEqual(1, _retry_delay(None, 0))  # 网关短退避不变。
            self.assertIsNone(_retry_delay("120", 0, rate_limited=True))
        error = ModelEndpointError(status_code=429, error_code="RateLimitReached", retry_after="120")
        with patch("backend.app.llm.http._request_json_sync", side_effect=error) as send, \
                patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(ModelEndpointError):
                asyncio.run(post_json("http://mock", {}, {}, 180))
            self.assertEqual(1, send.call_count)
            sleep.assert_not_called()

    def test_cancelling_rate_limit_wait_stops_before_retry(self) -> None:
        """取消可中断等待，不发送下一次请求，等待记录不能写为完成。"""
        error = ModelEndpointError(status_code=429, error_code="RateLimitReached")
        observer = Mock()
        with patch("backend.app.llm.http._request_json_sync", side_effect=error) as send, \
                patch("backend.app.llm.http.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)), \
                observe_requests(observer):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(post_json("http://mock", {}, {}, 180))
        self.assertEqual(1, send.call_count)
        self.assertFalse(observer.retry_wait.call_args.kwargs["completed"])

    @staticmethod
    def response_fixture() -> Mock:
        """构造成功 HTTP 替身，不发网络请求。"""
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        response.read.return_value = b'{"ok":true}'
        return response

    def test_success_timing_preserves_payload_and_timeout(self) -> None:
        """计时只观察原发送，不改变正文、参数和结果。"""
        observer = Mock()
        response = self.response_fixture()
        with patch("backend.app.llm.http.urlopen", return_value=response) as send, \
                patch("backend.app.llm.http.time.monotonic", side_effect=[10, 10.2, 10.2, 11.2, 11.4]), observe_requests(observer):
            self.assertEqual({"ok": True}, _request_json_sync("POST", "http://mock", {"max_tokens": 8192}, {}, 180))
        self.assertEqual(180, send.call_args.kwargs["timeout"])
        self.assertEqual({"max_tokens": 8192}, json.loads(send.call_args.args[0].data))
        timing = observer.received.call_args.kwargs["diagnostics"]
        self.assertEqual((200, 1000, 1400), (timing["headers_ms"], timing["body_ms"], timing["elapsed_ms"]))
        self.assertIsNone(timing["failure_phase"])

    def test_timeout_phase_and_logs_do_not_expose_exception_text(self) -> None:
        """等待响应头和读取响应体分别归类，但不声称定位到了代理/服务器根因。"""
        for during_read in (False, True):
            observer = Mock()
            response = self.response_fixture()
            response.read.side_effect = TimeoutError("PRIVATE_CREDENTIAL_ADDRESS")
            with patch("backend.app.llm.http.urlopen", return_value=response,
                       side_effect=None if during_read else TimeoutError("PRIVATE_CREDENTIAL_ADDRESS")), \
                    observe_requests(observer), self.assertLogs("backend.app.llm.http", level="WARNING") as logs:
                with self.assertRaises(ModelConnectionError):
                    _request_json_sync("POST", "http://mock", {}, {}, 180)
            timing = observer.failed.call_args.kwargs["diagnostics"]
            self.assertEqual("reading_response_body" if during_read else "awaiting_response_headers", timing["failure_phase"])
            self.assertEqual(during_read, timing["headers_received"])
            self.assertFalse(timing["body_received"])
            self.assertNotIn("PRIVATE", json.dumps(timing) + str(logs.output))
            observer.received.assert_not_called()
            self.assertEqual(1, observer.failed.call_count)

    def test_wrapped_network_error_has_safe_cause(self) -> None:
        """URLError 内层的类型/数字错误码保留，异常文本不保存。"""
        observer = Mock()
        with patch("backend.app.llm.http.urlopen", side_effect=URLError(ConnectionResetError(10054, "PRIVATE"))), observe_requests(observer):
            with self.assertRaises(ModelConnectionError):
                _request_json_sync("POST", "http://mock", {}, {}, 1)
        details = observer.failed.call_args.kwargs["diagnostics"]
        self.assertEqual("ConnectionResetError", details["cause_type"])
        self.assertEqual(10054, details["errno"])
        self.assertNotIn("PRIVATE", json.dumps(details))

    def test_http_error_metadata_and_error_body_read_failure(self) -> None:
        """HTTP 状态、允许错误字段及错误体读取失败分别保存，不推定空 503 来源。"""
        for fail_read in (False, True):
            body = Mock() if fail_read else BytesIO(b'{"error":{"type":"rate_limit_error","code":"slow_down","message":"PRIVATE"}}')
            if fail_read:
                body.read.side_effect = TimeoutError("PRIVATE")
            headers = Message()
            headers["x-request-id"] = "req_test"
            error = HTTPError("http://mock", 503 if fail_read else 429, "private status text", headers, body)
            observer = Mock()
            with patch("backend.app.llm.http.urlopen", side_effect=error), observe_requests(observer):
                with self.assertRaises(ModelEndpointError) as raised:
                    _request_json_sync("POST", "http://mock", {}, {}, 1)
            details = observer.failed.call_args.kwargs["diagnostics"]
            self.assertTrue(raised.exception.retryable)
            self.assertTrue(details["headers_received"])
            self.assertEqual(503 if fail_read else 429, details["status_code"])
            self.assertEqual("req_test", details["provider_error"]["request_id"])
            if fail_read:
                self.assertEqual("TimeoutError", details["error_body_read_error"])
            else:
                self.assertEqual("slow_down", details["provider_error"]["code"])
            self.assertNotIn("PRIVATE", json.dumps(details))

    def test_early_eof_is_audited_without_changing_exception(self) -> None:
        """额外协议异常可诊断，但不暗中将其变成可重试连接异常。"""
        response = self.response_fixture()
        response.read.side_effect = IncompleteRead(b"PRIVATE", 10)
        observer = Mock()
        with patch("backend.app.llm.http.urlopen", return_value=response), observe_requests(observer):
            with self.assertRaises(IncompleteRead):
                _request_json_sync("POST", "http://mock", {}, {}, 1)
        details = observer.failed.call_args.kwargs["diagnostics"]
        self.assertEqual("reading_response_body", details["failure_phase"])
        self.assertNotIn("PRIVATE", json.dumps(details))

    def test_empty_http_error_is_distinct_from_error_body_timeout(self) -> None:
        """确认读到空错误体与未能读到错误体不同；两者都不能直接定位 503 来源。"""
        error = HTTPError("http://mock", 503, "private", Message(), BytesIO(b""))
        observer = Mock()
        with patch("backend.app.llm.http.urlopen", side_effect=error), observe_requests(observer):
            with self.assertRaises(ModelEndpointError):
                _request_json_sync("POST", "http://mock", {}, {}, 1)
        details = observer.failed.call_args.kwargs["diagnostics"]
        self.assertEqual(0, details["error_body_bytes"])
        self.assertEqual("empty", details["error_body_format"])
        self.assertNotIn("error_body_read_error", details)

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
                _request_json_sync("POST", "https://example.test", {}, {}, 1.0)
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

    @staticmethod
    async def successful_probe(url, payload, headers, **kwargs):
        """模拟两轮 Responses 协议；随机校验值只能来自工具结果，不预设答案。"""
        if payload.get("tools"):
            return {"output": [{"type": "function_call", "call_id": "probe-call",
                                "name": "agent_compatibility_probe", "arguments": "{}"}]}
        token = json.loads(payload["input"][-1]["output"])["probe_token"]
        return {"status": "completed", "output_text": token, "output": []}

    def test_probe_reports_success(self) -> None:
        """最小生成成功时应同时确认连接、模型与生成能力。"""
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.providers.openai_provider.post_json",
            new=AsyncMock(side_effect=self.successful_probe),
        ) as request:
            result = asyncio.run(probe_model_connection())
        self.assertTrue(result["connected"])
        self.assertTrue(result["model_accessible"])
        self.assertTrue(result["generation_available"])
        self.assertTrue(result["agent_compatible"])
        self.assertEqual(request.await_args_list[0].args[1]["tool_choice"], "required")
        self.assertEqual(request.await_count, 2)
        self.assertTrue(result["tool_roundtrip_verified"])
        self.assertEqual(request.await_args.args[2]["Authorization"], "Bearer secret-key")
        self.assertNotIn("secret-key", json.dumps(result))

    def test_probe_uses_requested_model_override(self) -> None:
        """智能体页面选择的模型应进入探测请求并出现在安全结果中。"""
        with patch.dict(os.environ, self.environment, clear=True), patch(
            "backend.app.llm.providers.openai_provider.post_json",
            new=AsyncMock(side_effect=self.successful_probe),
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
            "backend.app.llm.providers.openai_provider.post_json",
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
            "backend.app.llm.providers.openai_provider.post_json",
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
                        "id": "probe-call",
                        "function": {"name": "agent_compatibility_probe", "arguments": "{}"},
                    }],
                },
            }],
        }
        async def compatible_probe(url, payload, headers, **kwargs):
            if payload.get("tools"):
                return response
            token = json.loads(payload["messages"][-1]["content"])["probe_token"]
            return {"choices": [{"finish_reason": "stop", "message": {"content": token}}]}
        with patch.dict(os.environ, environment, clear=True), patch(
            "backend.app.llm.providers.compatible_provider.post_json",
            new=AsyncMock(side_effect=compatible_probe),
        ) as request:
            result = asyncio.run(probe_model_connection())
        payload = request.await_args_list[0].args[1]
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
