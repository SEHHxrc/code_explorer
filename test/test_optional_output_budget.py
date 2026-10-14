"""省略输出上限的离线回归；仅用模拟 HTTP、内存数据库，不读取实际 .env。"""

import asyncio
import hashlib
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.run_store import AgentRunStore
from backend.app.api.model import get_model_status
from backend.app.experiments.audit.policy import visible_request
from backend.app.experiments.audit.recorder import AuditRecorder
from backend.app.experiments.audit.repository import AuditRepository, lock_writes
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.llm.budget import BudgetPlanner, ModelBudgetError, request_payload, validate_provider_request
from backend.app.llm.conversation import ModelMessage
from backend.app.llm.diagnostics import probe_model_connection
from backend.app.llm.observation import observe_requests
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm.registry import create_model_provider
from backend.app.llm.settings import ModelLimits, get_model_limits
from backend.app.models import AgentEventModel, AgentRunModel, Base
from test.probe_final_report_modes import probe
from test.test_experiment_quality import ANSWER
from test.validate_model_budget import validate, validate_output


OUTPUT_KEYS = {"max_tokens", "max_completion_tokens", "max_output_tokens"}
ENV = {"CODE_EXPLORER_LLM_PROVIDER": "compatible", "CODE_EXPLORER_LLM_MODEL": "mock",
       "CODE_EXPLORER_LLM_BASE_URL": "http://mock.invalid/v1", "CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": ""}
MESSAGES = [ModelMessage(role="user", content="question")]
TOOLS = [{"type": "function", "name": "read", "description": "read",
          "parameters": {"type": "object", "properties": {}}}]


class OptionalOutputBudgetTests(unittest.TestCase):
    """覆盖配置语义、协议省略、本地预算、审计恢复和有界探测。"""

    def setUp(self):
        self.environment = patch.dict(os.environ, ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def providers(self, limit=None):
        return [OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock.invalid", model="mock",
                                        max_output_tokens=limit, output_token_parameter=key)
                for key in ("max_tokens", "max_completion_tokens")] + [
                    OpenAIResponsesProvider(api_key="fake", model="mock", max_output_tokens=limit)]

    def test_absent_variable_keeps_existing_default(self):
        os.environ.pop("CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS")
        self.assertEqual(2400, get_model_limits().max_output_tokens)
        self.assertEqual(32768, get_model_limits().output_reserve_tokens)
        self.assertEqual(2400, create_model_provider().max_output_tokens)

    def test_explicit_empty_whitespace_and_quoted_empty_omit_limit(self):
        for value in ("", "  ", "''", '""', "' '"):
            with self.subTest(value=value), patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": value}):
                self.assertIsNone(get_model_limits().max_output_tokens)
                self.assertIsNone(create_model_provider().max_output_tokens)

    def test_finite_config_accepts_boundaries_and_rejects_invalid_values(self):
        for value in (128, 8192, 32768):
            with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": str(value)}):
                self.assertEqual(value, get_model_limits().max_output_tokens)
        for value in ("invalid", "0", "-1", "127", "32769", "1.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": value}):
                with self.assertRaises(ValueError):
                    get_model_limits()

    def test_reserve_config_is_strict_and_independent(self):
        for value in (128, 65536, 1000000):
            with patch.dict(os.environ, {"CODE_EXPLORER_LLM_OUTPUT_RESERVE_TOKENS": str(value)}):
                self.assertEqual(value, get_model_limits().output_reserve_tokens)
        for value in ("", "0", "127", "1000001", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"CODE_EXPLORER_LLM_OUTPUT_RESERVE_TOKENS": value}):
                with self.assertRaises(ValueError):
                    get_model_limits()

    def test_all_wire_variants_omit_limit_not_null_and_never_send_reserve(self):
        for provider in self.providers():
            payload = provider.request_payload(instructions="system", messages=MESSAGES, tools=TOOLS)
            self.assertFalse(OUTPUT_KEYS & payload.keys())
            self.assertNotIn("output_reserve_tokens", payload)
            self.assertIn("tools", payload)
            self.assertEqual(payload, visible_request(payload))
            self.assertIn("question", json.dumps(payload))

    def test_overrides_bound_probes_without_mutating_instance(self):
        for provider in self.providers():
            payload = provider.request_payload(instructions="system", messages=MESSAGES, tools=[], max_output_tokens=1024)
            key = next(iter(OUTPUT_KEYS & payload.keys()))
            self.assertEqual(1024, payload[key])
            self.assertIsNone(provider.max_output_tokens)
            self.assertFalse(OUTPUT_KEYS & provider.request_payload(instructions="s", messages=MESSAGES, tools=[]).keys())
        for provider in self.providers(8192):
            payload = provider.request_payload(instructions="s", messages=MESSAGES, tools=[], max_output_tokens=None)
            self.assertEqual([8192], [payload[key] for key in OUTPUT_KEYS if key in payload])

    def test_nonpositive_and_noninteger_method_overrides_fail(self):
        for provider in self.providers():
            for value in (0, -1, True, "8192", 1.5):
                with self.subTest(provider=provider.name, value=value), self.assertRaises(ValueError):
                    provider.request_payload(instructions="s", messages=MESSAGES, tools=[], max_output_tokens=value)

    def test_known_window_uses_local_reserve_only_when_limit_omitted(self):
        limits = ModelLimits(0, None, max_input_tokens=524288, context_window_tokens=524288)
        planner = BudgetPlanner(limits)
        self.assertEqual(491008, planner.input_limit)
        snapshot = planner.require({"messages": []})
        self.assertEqual((None, "provider_default", 32768, "planning_only_unknown_output"), tuple(
            snapshot[key] for key in ("max_output_tokens", "output_limit_mode", "output_reserve_tokens", "output_reserve_basis")))
        self.assertEqual("utf8-token-budget-v3-provider-default", snapshot["budget_version"])
        self.assertEqual(458240, BudgetPlanner(replace(limits, output_reserve_tokens=65536)).input_limit)
        finite = BudgetPlanner(replace(limits, max_output_tokens=8192, output_reserve_tokens=65536))
        self.assertEqual(515584, finite.input_limit)
        self.assertEqual("utf8-token-budget-v2", finite.require({})["budget_version"])
        self.assertEqual("request_limit", finite.require({})["output_reserve_basis"])

    def test_unknown_window_does_not_claim_capacity(self):
        planner = BudgetPlanner(ModelLimits(0, None))
        self.assertEqual(17488, planner.input_limit)
        self.assertEqual("unknown", planner.require({})["capacity_source"])

    def test_public_status_distinguishes_omission_from_unknown(self):
        status = asyncio.run(get_model_status(current_user={"user_id": "user"}))["data"]
        self.assertIsNone(status["max_output_tokens"])
        self.assertEqual("provider_default", status["output_limit_mode"])
        self.assertEqual(32768, status["output_reserve_tokens"])
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": "8192"}):
            finite = asyncio.run(get_model_status(current_user={"user_id": "user"}))["data"]
        self.assertEqual("explicit", finite["output_limit_mode"])
        self.assertEqual(8192, finite["output_reserve_tokens"])

    def test_unusable_window_or_reserve_is_rejected(self):
        for limits in (ModelLimits(0, None, context_window_tokens=32768),
                       ModelLimits(0, None, output_reserve_tokens=0)):
            with self.assertRaises(ModelBudgetError):
                BudgetPlanner(limits)

    def test_fallback_serializer_and_final_validation_do_not_invent_2400(self):
        payload = request_payload(None, instructions="s", messages=MESSAGES, tools=[], max_output_tokens=None)
        self.assertFalse(OUTPUT_KEYS & payload.keys())
        provider = create_model_provider()
        provider.model_limits = replace(provider.model_limits, context_window_tokens=5000)
        with self.assertRaises(ModelBudgetError):
            validate_provider_request(provider, payload)  # 不能凭空采用 2400，绕过 32768 的预留。
        bounded = {**payload, "max_tokens": 1024}
        validate_provider_request(provider, bounded)
        with self.assertRaises(ModelBudgetError):
            validate_provider_request(provider, {**payload, "max_tokens": None})

    def test_mocked_actual_http_bytes_and_audit_preserve_omission(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        repository = AuditRepository(sessions)
        with sessions() as session:
            lock_writes(session)
            repository.reserve_pair(session, "pair", "project", "user", ["run"])
            session.add(AgentRunModel(id="run", project_id="project", user_id="user", question="q", status="completed"))
            session.commit()
        recorder = AuditRecorder(repository, "run", "user")
        provider = create_model_provider()
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"model": "mock", "choices": [{"finish_reason": "stop",
            "message": {"content": "answer"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
        with patch("backend.app.llm.http.urlopen", return_value=response) as send, observe_requests(recorder):
            turn = asyncio.run(provider.generate_with_tools(instructions="s", messages=MESSAGES, tools=[]))
        body = send.call_args.args[0].data
        payload = json.loads(body)
        self.assertFalse(OUTPUT_KEYS & payload.keys())
        self.assertNotIn("output_reserve_tokens", payload)
        self.assertEqual(("stop", 15), (turn.metadata.finish_reason, turn.metadata.usage.total_tokens))
        recorded = repository.read("run", "user")["records"][0]["payload"]
        self.assertEqual(payload, recorded["visible"])
        self.assertEqual(hashlib.sha256(body).hexdigest(), recorded["wire_sha256"])

    def test_model_history_and_metrics_keep_mode_and_unknown_old_fields(self):
        engine = create_engine("sqlite://", poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        snapshot = BudgetPlanner(ModelLimits(0, None)).require({})
        with sessions() as session:
            session.add(AgentRunModel(id="run", project_id="project", user_id="user", question="q", status="completed"))
            session.add(AgentEventModel(run_id="run", sequence=1, event_type="model.started", payload=snapshot))
            session.commit()
        restored = AgentRunStore(sessions).snapshot("run", "user").events[0].payload
        metrics = collect_run_metrics("run", "user", session_factory=sessions)["budget_configurations"][0]
        for key in ("max_output_tokens", "output_limit_mode", "output_reserve_tokens", "output_reserve_basis"):
            self.assertEqual(snapshot[key], restored[key])
            self.assertEqual(snapshot[key], metrics[key])
        with sessions() as session:
            row = session.query(AgentEventModel).first()
            row.payload = {key: value for key, value in snapshot.items() if key not in (
                "output_limit_mode", "output_reserve_tokens", "output_reserve_basis")}
            session.commit()
        old = collect_run_metrics("run", "user", session_factory=sessions)["budget_configurations"][0]
        self.assertIsNone(old["output_limit_mode"])
        self.assertIsNone(old["output_reserve_tokens"])

    def test_connectivity_probe_remains_explicitly_bounded(self):
        payloads = []

        async def respond(url, payload, headers, **kwargs):
            payloads.append(payload)
            if len(payloads) == 1:
                message = {"content": None, "tool_calls": [{"id": "probe", "type": "function", "function": {
                    "name": "agent_compatibility_probe", "arguments": "{}"}}]}
                finish = "tool_calls"
            else:
                message = {"content": json.loads(payload["messages"][-1]["content"])["probe_token"]}
                finish = "stop"
            return {"choices": [{"message": message, "finish_reason": finish}]}

        with patch("backend.app.llm.providers.compatible_provider.post_json", side_effect=respond):
            self.assertTrue(asyncio.run(probe_model_connection())["agent_compatible"])
        self.assertEqual([1024, 1024], [payload["max_tokens"] for payload in payloads])

    def test_report_mode_probe_is_bounded_and_long_output_probe_requires_cap(self):
        with patch("backend.app.llm.providers.compatible_provider.post_json", new=AsyncMock(return_value={
            "choices": [{"message": {"content": ANSWER}, "finish_reason": "stop"}]})) as send, \
                patch("builtins.print"):
            rows = asyncio.run(probe())
        self.assertEqual(2, len(rows))
        self.assertEqual([4096, 4096], [call.args[1]["max_tokens"] for call in send.call_args_list])
        provider = create_model_provider()
        with patch.object(provider, "generate_with_tools", new=AsyncMock()) as send, \
                patch("test.validate_model_budget.create_model_provider", return_value=provider):
            with self.assertRaises(ValueError):
                asyncio.run(validate_output(provider))
            with self.assertRaises(ValueError):
                asyncio.run(validate())
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
