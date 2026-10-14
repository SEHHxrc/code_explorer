"""单变量输出诊断预检与响应通道隐私回归；不使用真实 .env 或数据库。"""

import copy
import json
import os
import unittest
from unittest.mock import patch

from backend.app.agents.reporting import execution_policy, phase_message
from backend.app.experiments.audit.policy import digest, contract_digest
from backend.app.experiments.context import SecurityExperimentContextBuilder, prepare_experiment_artifact, SECURITY_EXPERIMENT_INSTRUCTIONS
from backend.app.experiments.report import REPORT_VERSION
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.llm.budget import BudgetPlanner, initial_message
from backend.app.llm.registry import create_model_provider, get_model_limits
from backend.app.llm.response_metadata import response_channel_sizes
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from test.diagnose_output_budget import verify_conditions, verify_report_mode_conditions, verify_timeout_conditions, safe_timeout_frames
from urllib.error import URLError


class OutputBudgetDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "CODE_EXPLORER_LLM_PROVIDER": "compatible", "CODE_EXPLORER_LLM_MODEL": "mock",
            "CODE_EXPLORER_LLM_BASE_URL": "http://mock", "CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": "8192",
            "CODE_EXPLORER_LLM_MAX_INPUT_TOKENS": "524288", "CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS": "524288",
            "CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS": "0", "CODE_EXPLORER_LLM_FINAL_REPORT_MODE": "tools_none",
            "CODE_EXPLORER_LLM_REASONING_EFFORT": "", "CODE_EXPLORER_LLM_TOKENIZER": "auto",
            "CODE_EXPLORER_LLM_TOKENIZER_PATH": "", "CODE_EXPLORER_LLM_TOKEN_MARGIN": "512",
            "CODE_EXPLORER_LLM_OUTPUT_TOKEN_PARAMETER": "max_tokens",
        })
        self.environment.start()
        self.artifact = {"security_evidence": SecurityEvidencePack().model_dump()}
        self.source = {"id": "old-pair", "project_id": "p", "question": "q", "lanes": {}}
        provider, limits = create_model_provider(), get_model_limits()
        schemas = create_experiment_tool_registry().schemas()
        policy = execution_policy(4, "tools_none")
        for strategy in ("baseline", "security_evidence"):
            packet = SecurityExperimentContextBuilder(with_evidence=strategy != "baseline").build(project_id="p", question="q",
                artifact=prepare_experiment_artifact(self.artifact, with_evidence=strategy != "baseline"))
            payload = provider.request_payload(instructions=SECURITY_EXPERIMENT_INSTRUCTIONS,
                messages=[initial_message("q", packet.prompt_context), phase_message(remaining_rounds=4)],
                tools=schemas, max_output_tokens=8192)
            self.source["lanes"][strategy] = {"run": {"model": "mock", "question": "q", "max_steps": 4},
                "budget": BudgetPlanner(limits).require(payload), "manifest": {"static_artifact_sha256": digest(self.artifact)},
                "started": {"execution_policy": policy, "contract_hash": contract_digest(SECURITY_EXPERIMENT_INSTRUCTIONS, schemas, REPORT_VERSION, policy)},
                "request": {"sha256": digest(payload)}}

    def tearDown(self):
        self.environment.stop()

    def test_preflight_has_no_write_or_output_configuration_change(self):
        original = copy.deepcopy(self.source)
        plan = verify_conditions(self.source, self.artifact, 16384)
        self.assertEqual(original, self.source)
        self.assertEqual("8192", os.environ["CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS"])
        self.assertFalse(plan["formal_effectiveness_scoring"])
        self.assertTrue(all(item["first_request_matches"] for item in plan["checks"].values()))

    def test_report_mode_change_is_rejected(self):
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_FINAL_REPORT_MODE": "history_no_tools"}):
            with self.assertRaises(ValueError):
                verify_conditions(self.source, self.artifact, 16384)

    def test_separate_mode_trial_preserves_output_and_source_conditions(self):
        original = copy.deepcopy(self.source)
        plan = verify_report_mode_conditions(self.source, self.artifact, "history_no_tools")
        self.assertEqual("final_report_mode", plan["changed_variable"])
        self.assertEqual(8192, plan["target_output_tokens"])
        self.assertEqual("tools_none", plan["original_final_report_mode"])
        self.assertEqual("history_no_tools", plan["target_final_report_mode"])
        self.assertEqual(original, self.source)
        self.assertEqual("tools_none", os.environ["CODE_EXPLORER_LLM_FINAL_REPORT_MODE"])
        with self.assertRaises(ValueError):
            verify_report_mode_conditions(self.source, self.artifact, "tools_none")
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": "16384"}):
            with self.assertRaises(ValueError):
                verify_report_mode_conditions(self.source, self.artifact, "history_no_tools")

    def test_model_input_or_artifact_change_is_rejected(self):
        source = copy.deepcopy(self.source)
        source["lanes"]["baseline"]["request"]["sha256"] = "different"
        with self.assertRaises(ValueError):
            verify_conditions(source, self.artifact, 16384)
        with self.assertRaises(ValueError):
            verify_conditions(self.source, {**self.artifact, "new_fact": True}, 16384)

    def test_input_budget_change_is_rejected(self):
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_INPUT_TOKENS": "300000"}):
            with self.assertRaises(ValueError):
                verify_conditions(self.source, self.artifact, 16384)

    def test_timeout_diagnostic_is_separate_and_rejects_other_changes(self):
        from backend.app.llm.http import RETRY_POLICY_VERSION, MAX_TRANSIENT_RETRIES, MAX_RATE_LIMIT_WAIT_SECONDS
        self.source["transport_retry_policy"] = {"version": RETRY_POLICY_VERSION, "max_retries": MAX_TRANSIENT_RETRIES,
            "max_rate_limit_wait_seconds": MAX_RATE_LIMIT_WAIT_SECONDS, "fallback_429_wait_seconds": [30, 60]}
        for lane in self.source["lanes"].values():
            lane["timeout_seconds"] = 180
        plan = verify_timeout_conditions(self.source, self.artifact, 300)
        self.assertEqual("request_timeout", plan["changed_variable"])
        self.assertEqual(8192, plan["target_output_tokens"])
        self.assertEqual("tools_none", plan["target_final_report_mode"])
        self.source["transport_retry_policy"]["max_retries"] = 0
        with self.assertRaises(ValueError):
            verify_timeout_conditions(self.source, self.artifact, 300)
        self.source["transport_retry_policy"]["max_retries"] = MAX_TRANSIENT_RETRIES
        for value in (180, 0, 601, float("nan")):
            with self.assertRaises(ValueError):
                verify_timeout_conditions(self.source, self.artifact, value)
        self.source["lanes"]["baseline"]["timeout_seconds"] = 120
        with self.assertRaises(ValueError):
            verify_timeout_conditions(self.source, self.artifact, 300)

    def test_timeout_trace_does_not_copy_source_locals_or_exception_text(self):
        secret = "PRIVATE_API_KEY_ADDRESS"
        try:
            raise TimeoutError(secret)
        except TimeoutError as error:
            frames = safe_timeout_frames(URLError(error))
        text = json.dumps(frames)
        self.assertNotIn(secret, text)
        self.assertTrue(frames)
        self.assertTrue(all(set(frame) == {"module", "function", "line"} for frame in frames))
        self.assertEqual([], safe_timeout_frames(URLError("PRIVATE_ADDRESS_CONNECTION_REFUSED")))

    def test_hidden_channel_is_measured_without_copying_content(self):
        result = response_channel_sizes({"choices": [{"message": {"role": "assistant", "content": None,
            "reasoning_content": "PRIVATE_REASONING", "thinking": "PRIVATE_THINKING"}}]})
        channel = result["chat_messages"][0]
        self.assertEqual(len("PRIVATE_REASONING"), channel["fields"]["reasoning_content"]["characters"])
        self.assertIsNone(channel["fields"]["content"]["characters"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual("protocol_field_characters_not_token_accounting", result["measurement"])

    def test_malformed_optional_channels_do_not_break_audit(self):
        for choices in (None, 7, "invalid", {}, [None, {"message": "invalid"}]):
            self.assertEqual([], response_channel_sizes({"choices": choices})["chat_messages"])


if __name__ == "__main__":
    unittest.main()
