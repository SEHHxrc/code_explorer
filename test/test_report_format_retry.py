"""共同格式重提交协议；模拟模型，不修复原文、不调用真实端点。"""

import asyncio
import copy
from dataclasses import replace
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.reporting import execution_policy
from backend.app.agents.contracts import AgentRunRequest, ContextPacket
from backend.app.agents.tools.base import ToolContext, ToolRegistry
from backend.app.agents.tools.batches import SubmitAnalysisBatchTool
from backend.app.experiments.findings import LedgerValidationError, parse_ledger
from backend.app.experiments.report import BATCH_PROTOCOL, REPORT_INSTRUCTIONS, format_retry_message
from backend.app.llm.base import ModelOutputError, ModelTurn, ToolCall
from backend.app.llm.budget import BudgetPlanner, request_payload
from backend.app.llm.response_metadata import ModelResponseMetadata
from backend.app.llm.conversation import chat_messages, response_items, validate_messages
from backend.app.llm.settings import ModelLimits
from test.test_agent_foundation import MemoryRunStore
from test.test_findings_ledger import finding
from test.test_on_demand_evidence import LEDGER_JSON, STOP


class ReportFormatRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store = MemoryRunStore()
        self.manager = AgentRunManager(store=self.store)
        self.planner = BudgetPlanner(ModelLimits(0, None))
        self.messages = [{"role": "user", "content": "共同问题及已展示证据。"}]

    async def generate(self, *turns: ModelTurn | BaseException, report: bool = True,
                       run_id: str = "run", report_mode: str = "tools_none",
                       tools: list[dict] | None = None) -> tuple[ModelTurn, SimpleNamespace]:
        """复用生产生成路径；模型替身只返回给定轮次，不执行任何工具。"""
        provider = SimpleNamespace(name="mock", model="mock", generate_with_tools=AsyncMock(side_effect=turns))
        tools = tools or []
        payload = request_payload(provider, instructions=REPORT_INSTRUCTIONS, messages=self.messages,
                                  tools=tools, max_output_tokens=None)
        result = await self.manager._generate(provider, payload, 1, run_id=run_id,
            report_planner=self.planner if report else None, report_mode=report_mode, instructions=REPORT_INSTRUCTIONS,
            messages=self.messages, tools=tools)
        return result, provider

    @staticmethod
    def turn(text: str, metadata: ModelResponseMetadata = STOP) -> ModelTurn:
        return ModelTurn(text, "mock", "mock", metadata=metadata)

    async def test_valid_report_has_no_extra_request(self) -> None:
        result, provider = await self.generate(self.turn(LEDGER_JSON))
        self.assertEqual(1, provider.generate_with_tools.await_count)
        self.assertEqual(LEDGER_JSON, result.text)

    async def test_broken_json_resubmits_once_without_mutating_original_or_evidence(self) -> None:
        original = self.turn(LEDGER_JSON[:-1])
        audit = Mock()
        self.manager.audit = audit
        result, provider = await self.generate(original, self.turn(LEDGER_JSON))
        self.assertEqual(2, provider.generate_with_tools.await_count)
        retry = provider.generate_with_tools.call_args_list[1].kwargs
        self.assertEqual(self.messages, retry["messages"][:1])
        self.assertEqual(original.text, retry["messages"][1]["content"])
        self.assertEqual("none", retry["tool_choice"])
        self.assertEqual([], retry["tools"])
        self.assertIn("不读取文件", retry["messages"][-1]["content"])
        self.assertEqual(LEDGER_JSON[:-1], original.text)
        self.assertEqual(2, audit.turn.call_count)
        self.assertEqual(original.text, audit.turn.call_args_list[0].args[0])
        completed = [p for kind, p in self.store.events if kind == "model.completed"]
        self.assertEqual(2, len(completed))
        self.assertEqual(280, sum(p["metadata"]["usage"]["total_tokens"] for p in completed))
        answer = self.manager._accept_answer("run", result, forbid_tools=True, require_report=True)
        self.assertEqual(parse_ledger(LEDGER_JSON).render(), answer)
        self.assertEqual(1, sum(kind == "findings.recorded" for kind, _ in self.store.events))

    async def test_misnested_root_fields_supply_explicit_schema_paths(self) -> None:
        raw = json.loads(LEDGER_JSON)
        for key in ("reviewed_ranges", "unreviewed", "limitations"):
            raw["coverage"][key] = raw.pop(key)
        result, provider = await self.generate(self.turn(json.dumps(raw)), self.turn(LEDGER_JSON))
        self.assertEqual(LEDGER_JSON, result.text)
        message = provider.generate_with_tools.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn('"category":"schema"', message)
        self.assertIn("reviewed_ranges", message)
        self.assertIn("必须与 coverage 同级", message)

    async def test_second_invalid_answer_does_not_loop_or_publish(self) -> None:
        result, provider = await self.generate(self.turn("{broken"), self.turn("{still broken"))
        self.assertEqual(2, provider.generate_with_tools.await_count)
        with self.assertRaises(ModelOutputError):
            self.manager._accept_answer("run", result, require_report=True)
        self.assertFalse(any(kind == "findings.recorded" for kind, _ in self.store.events))
        # 同一次运行后续批次也不能获得第二次格式重提交。
        _, later = await self.generate(self.turn("{another broken"))
        self.assertEqual(1, later.generate_with_tools.await_count)

    async def test_state_conflicts_require_explicit_model_resubmission_and_keep_unknown_conditions(self) -> None:
        raw = json.loads(LEDGER_JSON)
        item = finding()
        item['unresolved_conditions'].append({'kind': 'security_requirement', 'description': '资源是否应限制访问尚不明确。'})
        raw["findings"] = [item]
        corrected = copy.deepcopy(raw)
        corrected['findings'][0]['status'] = 'unclosed_candidate'
        original = self.turn(json.dumps(raw))
        result, provider = await self.generate(original, self.turn(json.dumps(corrected)))
        self.assertEqual(2, provider.generate_with_tools.await_count)
        self.assertEqual(corrected, parse_ledger(result.text).model_dump())
        self.assertEqual(raw, json.loads(original.text))
        self.assertEqual(raw['findings'][0]['unresolved_conditions'], corrected['findings'][0]['unresolved_conditions'])
        feedback = provider.generate_with_tools.call_args_list[1].kwargs['messages'][-1]['content']
        self.assertIn('"category":"state"', feedback)
        self.assertIn('不得删除条目/未知条件', feedback)
        with self.assertRaises(LedgerValidationError) as caught:
            parse_ledger(original.text)
        self.assertEqual("state", caught.exception.category)

    @staticmethod
    def submission(text: str, call_id: str = 'submit') -> ModelTurn:
        return ModelTurn('', 'mock', 'mock', (ToolCall(call_id, 'submit_analysis_batch', {
            'ledger_json': text, 'evidence_ids': [], 'scopes': ['app.py:1-2']}),),
            metadata=ModelResponseMetadata(finish_reason='tool_calls'))

    async def test_batch_tool_state_rejection_is_paired_and_only_valid_resubmission_is_published(self) -> None:
        """完整报告阶段复现：拒绝不一致台账后才重提，未知前提保留，不推进失败批次。"""
        raw = json.loads(LEDGER_JSON)
        item = finding()
        item['unresolved_conditions'].append({'kind': 'security_requirement', 'description': '授权要求未知。'})
        raw['findings'] = [item]
        corrected = copy.deepcopy(raw)
        corrected['findings'][0]['status'] = 'unclosed_candidate'
        original, valid = self.submission(json.dumps(raw), 'rejected'), self.submission(json.dumps(corrected), 'accepted')
        context = ToolContext('project', 'user', Path('.'), {})
        context.batches.scopes.add('app.py:1-2')
        self.manager.tools = ToolRegistry([SubmitAnalysisBatchTool()])

        async def respond(**kwargs):
            self.assertFalse(context.batches.reports)
            self.assertFalse(context.batches.completed_scopes)
            return original if 'APPLICATION_REPORT_FORMAT_RETRY' not in kwargs['messages'][-1].get('content', '') else valid

        provider = SimpleNamespace(name='mock', model='mock', generate_with_tools=AsyncMock(side_effect=respond))
        result = await self.manager._report_batch('run', provider, self.planner, self.messages[0], [],
            self.manager.tools.schemas(), 'tools_none', context)
        self.assertIs(valid, result)
        self.assertEqual(2, provider.generate_with_tools.await_count)
        retry = provider.generate_with_tools.call_args_list[1].kwargs
        validate_messages(retry['messages'])
        self.assertEqual('auto', retry['tool_choice'])
        self.assertEqual(['submit_analysis_batch'], [tool['name'] for tool in retry['tools']])
        feedback = next(message for message in retry['messages'] if message.get('tool_call_id') == 'rejected')
        self.assertFalse(json.loads(feedback['content'])['published'])
        self.assertEqual(1, len(context.batches.reports))
        self.assertEqual('unclosed_candidate', context.batches.reports[0]['ledger']['findings'][0]['status'])
        self.assertEqual({'app.py:1-2'}, context.batches.completed_scopes)
        self.assertEqual(1, sum(kind == 'batch.completed' for kind, _ in self.store.events))
        self.assertEqual(1, sum(kind == 'tool.failed' for kind, _ in self.store.events))
        self.assertEqual(raw, json.loads(original.tool_calls[0].arguments['ledger_json']))

    async def test_batch_tool_argument_and_ledger_syntax_use_the_same_bounded_retry(self) -> None:
        tools = [SubmitAnalysisBatchTool().schema()]
        invalid_arguments = ModelTurn('', 'mock', 'mock', (ToolCall('invalid', 'submit_analysis_batch', {'ledger_json': {}}),),
                                     metadata=ModelResponseMetadata(finish_reason='tool_calls'))
        for invalid in (invalid_arguments, self.submission('{')):
            with self.subTest(arguments=invalid.tool_calls[0].arguments), patch.object(self.manager, '_generation_states', {}):
                valid = self.submission(LEDGER_JSON, 'valid')
                result, provider = await self.generate(invalid, valid, report_mode='batch_submit_only', tools=tools)
                self.assertIs(valid, result)
                self.assertEqual(2, provider.generate_with_tools.await_count)
                validate_messages(provider.generate_with_tools.call_args_list[1].kwargs['messages'])

    async def test_rejected_native_submission_preserves_responses_items_and_chat_pairing(self) -> None:
        """原生续接状态和工具拒绝结果必须同时可序列化，不制造孤立 function_call_output。"""
        original = self.submission('{', 'native-rejected')
        call = original.tool_calls[0]
        function = {'name': call.name, 'arguments': json.dumps(call.arguments)}
        original = replace(original, metadata=ModelResponseMetadata(response_status='completed'), continuation={
            'role': 'assistant', 'content': None,
            'tool_calls': [{'id': call.id, 'type': 'function', 'function': function}],
            'provider_items': [{'type': 'reasoning', 'id': 'synthetic-reasoning', 'encrypted_content': 'synthetic-state'},
                               {'type': 'function_call', 'call_id': call.id, **function}]})
        _, provider = await self.generate(original, self.submission(LEDGER_JSON), report_mode='batch_submit_only',
                                          tools=[SubmitAnalysisBatchTool().schema()])
        messages = provider.generate_with_tools.call_args_list[1].kwargs['messages']
        items = response_items(messages)
        self.assertEqual(1, sum(item.get('type') == 'function_call' for item in items))
        self.assertEqual([call.id], [item['call_id'] for item in items if item.get('type') == 'function_call_output'])
        self.assertIn('synthetic-reasoning', [item.get('id') for item in items])
        self.assertEqual(1, sum(message['role'] == 'tool' for message in chat_messages('report', messages)))

    async def test_repeated_state_conflicts_remain_invalid_and_do_not_receive_unlimited_retries(self) -> None:
        raw = json.loads(LEDGER_JSON)
        raw['findings'] = [finding()]
        raw['findings'][0]['unresolved_conditions'].append({'kind': 'other', 'description': '未核实条件。'})
        invalid = self.turn(json.dumps(raw))
        result, provider = await self.generate(invalid, invalid)
        self.assertEqual(2, provider.generate_with_tools.await_count)
        with self.assertRaises(LedgerValidationError):
            parse_ledger(result.text)
        _, later = await self.generate(invalid)
        self.assertEqual(1, later.generate_with_tools.await_count)

    async def test_incomplete_or_mixed_batch_tools_are_not_repaired_or_executed(self) -> None:
        valid = self.submission(LEDGER_JSON)
        for turn in (ModelTurn('', 'mock', 'mock', valid.tool_calls,
                metadata=ModelResponseMetadata(finish_reason='length')),
                ModelTurn('', 'mock', 'mock', (*valid.tool_calls, ToolCall('read', 'read_file_range', {})),
                          metadata=ModelResponseMetadata(finish_reason='tool_calls')),
                ModelTurn('', 'mock', 'mock', (ToolCall('read', 'read_file_range', {}),),
                          metadata=ModelResponseMetadata(finish_reason='tool_calls'))):
            with self.subTest(reason=turn.metadata.finish_reason):
                self.manager._generation_states.clear()
                _, provider = await self.generate(turn, report_mode='batch_submit_only')
                self.assertEqual(1, provider.generate_with_tools.await_count)
        self.assertFalse(any(kind == 'tool.completed' for kind, _ in self.store.events))

    async def test_empty_truncated_unknown_or_tool_finished_outputs_are_not_format_retried(self) -> None:
        for text, metadata in (("", STOP), (LEDGER_JSON[:-1], ModelResponseMetadata(finish_reason="length")),
                               ("{", ModelResponseMetadata()), ("{", ModelResponseMetadata(finish_reason="tool_calls"))):
            with self.subTest(reason=metadata.finish_reason):
                _, provider = await self.generate(self.turn(text, metadata))
                self.assertEqual(1, provider.generate_with_tools.await_count)

    async def test_retry_budget_failure_does_not_trim_source_or_change_limits(self) -> None:
        self.planner.fits = Mock(return_value=False)
        result, provider = await self.generate(self.turn(LEDGER_JSON[:-1]))
        self.assertEqual(1, provider.generate_with_tools.await_count)
        self.assertEqual(LEDGER_JSON[:-1], result.text)
        self.assertEqual(0, self.manager._generation_states["run"].format_retries_used)
        self.assertTrue(any(kind == "report.format_retry_skipped" for kind, _ in self.store.events))

    async def test_tool_request_in_format_retry_is_never_executed(self) -> None:
        unexpected = ModelTurn("", "mock", "mock", (ToolCall("bad", "read_file_range", {}),),
                               metadata=ModelResponseMetadata(finish_reason="tool_calls"))
        with self.assertRaises(ModelOutputError) as caught:
            await self.generate(self.turn("{"), unexpected)
        self.assertEqual("unexpected_tool_calls", caught.exception.code)
        self.assertIs(unexpected, self.manager._generation_states["run"].last_turn)
        self.assertFalse(any(kind == "tool.requested" for kind, _ in self.store.events))

    async def test_ordinary_answers_do_not_gain_a_format_retry(self) -> None:
        _, provider = await self.generate(self.turn("普通自由文本。"), report=False)
        self.assertEqual(1, provider.generate_with_tools.await_count)

    async def test_concurrent_runs_do_not_share_retry_quota_or_latest_response(self) -> None:
        first, second = self.turn(LEDGER_JSON), self.turn(LEDGER_JSON)
        results = await asyncio.gather(
            self.generate(self.turn("{"), first, run_id="a"),
            self.generate(self.turn("{"), second, run_id="b"))
        self.assertEqual([2, 2], [provider.generate_with_tools.await_count for _, provider in results])
        self.assertIs(first, self.manager._generation_states["a"].last_turn)
        self.assertIs(second, self.manager._generation_states["b"].last_turn)
        self.assertEqual([1, 1], [self.manager._generation_states[key].format_retries_used for key in ("a", "b")])

    async def test_run_integration_uses_latest_metadata_and_never_executes_retry_tools(self) -> None:
        """覆盖真实编排分支：恢复成功或安全失败，不把重提交的工具带回调查循环。"""
        bad_tool = ModelTurn("", "mock", "mock", (ToolCall("bad", "read_file_range", {}),),
                             metadata=ModelResponseMetadata(finish_reason="tool_calls"))
        for retry in (self.turn(LEDGER_JSON), bad_tool):
            with self.subTest(tool_violation=bool(retry.tool_calls)):
                store = MemoryRunStore()
                provider = SimpleNamespace(name="mock", model="mock", generate_with_tools=AsyncMock(
                    side_effect=[self.turn("{"), retry]))
                builder = SimpleNamespace(build=Mock(return_value=ContextPacket(
                    project_id="p", project_name="fixture", prompt_context="共同证据。", manifest={}, repo_map="")))
                manager = AgentRunManager(store=store, context_builder=builder, tools=ToolRegistry([]),
                                          instructions=REPORT_INSTRUCTIONS)
                with tempfile.TemporaryDirectory() as directory, patch(
                        "backend.app.agents.orchestrator.create_model_provider", return_value=provider):
                    await manager._run(run_id="run", project_id="p", user_id="u", project_root=directory,
                        artifact={"_experiment_protocol": BATCH_PROTOCOL}, request=AgentRunRequest(question="q", max_steps=1))
                self.assertEqual(2, provider.generate_with_tools.await_count)
                self.assertFalse(any(kind == "tool.requested" for kind, _ in store.events))
                self.assertEqual("failed" if retry.tool_calls else "completed", store.status)
                self.assertEqual({}, manager._generation_states)
                if retry.tool_calls:
                    failure = next(p for kind, p in store.events if kind == "run.failed")
                    self.assertEqual("unexpected_tool_calls", failure["error_code"])
                    self.assertEqual("tool_calls", failure["final_response_metadata"]["finish_reason"])

    async def test_cancellation_is_not_swallowed(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await self.generate(self.turn("{"), asyncio.CancelledError())

    def test_diagnostics_keep_location_not_input_values_and_policy_is_explicit(self) -> None:
        with self.assertRaises(LedgerValidationError) as caught:
            parse_ledger("{\ninvalid")
        self.assertEqual("syntax", caught.exception.category)
        self.assertEqual(2, caught.exception.issues[0]["line"])
        self.assertNotIn('"input":', format_retry_message(caught.exception))
        for mode in ("tools_none", "history_no_tools"):
            self.assertEqual(1, execution_policy(8, mode)["report_format"]["max_extra_requests"])


if __name__ == "__main__":
    unittest.main()
