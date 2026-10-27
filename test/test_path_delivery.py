"""路径传输/声明分离与无总字符上限报告的离线回归；不调用真实模型、不改旧实验。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from backend.app.agents.batching import AnalysisBatches
from backend.app.agents.reporting import execution_policy
from backend.app.agents.tools.batches import BatchReportArguments
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.conversation_window import ToolRound, round_messages
from backend.app.agents.path_delivery import PathDeliveryObserver
from backend.app.agents.tools.base import ToolContext
from backend.app.llm.base import ModelOutputError, ModelTurn, ToolCall
from backend.app.llm.response_metadata import ModelResponseMetadata
from backend.app.llm.observation import WIRE_OBSERVER
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog
from test.test_agent_foundation import MemoryRunStore
from test.test_chain_acceptance import NonNativeResolver
from test.test_on_demand_evidence import fixture_pack, LEDGER_JSON, STOP


class PathDeliveryTests(unittest.TestCase):
    """显式区分返回、发送尝试、响应、模型请求及字面引用，不测试模型语义正确性。"""

    def setUp(self) -> None:
        self.events = []
        self.observer = PathDeliveryObserver(lambda name, payload: self.events.append((name, payload)))
        self.catalog = SecurityEvidenceCatalog(fixture_pack().model_dump())

    def test_tool_result_without_followup_request_is_not_sent(self) -> None:
        self.observer.returned("call", self.catalog.detail("c1", section="flow", limit=2))
        self.observer.model_observed(ModelTurn("结束", "fixture", "fixture", metadata=STOP))
        self.assertEqual(["paths.returned", "paths.model_observed"], [kind for kind, _ in self.events])
        self.assertFalse(self.observer.pending)
        self.assertNotIn("used", json.dumps(self.events))

    def test_initial_summary_only_delivers_family_navigation(self) -> None:
        summary = self.catalog.summary()
        identifier = self.observer.sent({"messages": [{"role": "user", "content": "q\nSTATIC_SECURITY_EVIDENCE\n" + summary + "\nCONVERSATION_WINDOW\n{}"}]}, 1)
        references = self.events[-1][1]["references"]
        self.assertEqual({"family_summary"}, {item["representation"] for item in references})
        self.assertNotIn("c1", {item["id"] for item in references})
        self.observer.received(identifier, {})
        self.assertEqual("unknown", self.events[-1][1]["model_use"])

    def test_chat_and_responses_record_exact_section_and_pagination(self) -> None:
        page = self.catalog.detail("c1", section="flow", offset=2, limit=2)
        text = json.dumps({"content": page, "evidence": []})
        for message in ({"role": "tool", "tool_call_id": "call", "content": text},
                        {"type": "function_call_output", "call_id": "call", "output": text}):
            key = "messages" if "role" in message else "input"
            identifier = self.observer.sent({key: [message]}, 1)
            ref = self.events[-1][1]["references"][0]
            self.assertEqual(("c1", "flow", 2, 4, [2, 3], True),
                             (ref["id"], ref["section"], ref["offset"], ref["next_offset"], ref["item_orders"], ref["has_more"]))
            self.assertEqual("call", ref["tool_call_id"])
            self.assertNotIn("expression", json.dumps(ref))
            self.observer.received(identifier, {})

    def test_compacted_payload_never_records_omitted_steps(self) -> None:
        page = self.catalog.detail("c1", section="flow", limit=5)
        page["items"] = page["items"][:1]  # 发送投影不是完整工具返回，保留分页端点但不得声称全部交付。
        self.observer.sent({"messages": [{"role": "tool", "content": json.dumps({"content": page,
            "observation_window": {"content_omitted": True}})}]}, 1)
        ref = self.events[-1][1]["references"][0]
        self.assertEqual([0], ref["item_orders"])
        self.assertEqual(1, ref["item_count"])
        self.assertTrue(ref["content_omitted"])

    def test_archive_delivery_is_distinct_from_model_ledger(self) -> None:
        page = self.catalog.detail("c1")
        archive = {"observations": [{"batch_id": 1, "result": {"content": page}}]}
        self.observer.sent({"input": [{"role": "user", "content": "APPLICATION_BATCH_EVIDENCE\n" + json.dumps(archive)},
                                     {"role": "assistant", "content": "STATIC_SECURITY_EVIDENCE\n" + self.catalog.summary()}]}, 1)
        refs = self.events[-1][1]["references"]
        self.assertEqual(["c1"], [ref["id"] for ref in refs])
        self.assertEqual(1, refs[0]["archive_batch_id"])

    def test_report_projection_delivery_preserves_page_and_omission_metadata(self) -> None:
        page = self.catalog.detail('c1', section='flow', offset=2, limit=2)
        result = {'content': page, 'observation_window': {'content_omitted': True}}
        round_ = ToolRound({'role':'assistant','content':None,'tool_calls':[{'id':'original-call'}]},
            [{'call_id':'original-call','name':'get_static_security_evidence','arguments':{},'result':result}])
        self.observer.sent({'messages':round_messages(round_, report_only=True)}, 1)
        ref = self.events[-1][1]['references'][0]
        self.assertEqual(('c1','flow',2,[2,3]), (ref['id'],ref['section'],ref['offset'],ref['item_orders']))
        self.assertEqual('original-call', ref['observation_id'])
        self.assertTrue(ref['content_omitted'])
        self.assertNotIn('tool_call_id', ref)

    def test_failure_and_retry_forward_audit_without_claiming_reception(self) -> None:
        delegate = Mock()
        delegate.sent.return_value = 123
        self.observer.delegate = delegate
        identifier = self.observer.sent({"messages": []}, 1, wire_sha256="hash", wire_bytes=33)
        self.observer.failed(identifier, "HTTPError", diagnostics={"status_code": 503})
        self.observer.retry_wait(1, 2.0, 2.0, status_code=503, completed=True)
        self.assertEqual("failed", self.events[-1][1]["status"])
        self.assertEqual("unknown", self.events[-1][1]["model_use"])
        delegate.sent.assert_called_once()
        delegate.failed.assert_called_once_with(123, "HTTPError", diagnostics={"status_code": 503})
        delegate.retry_wait.assert_called_once()
        delegate.received.assert_not_called()
        self.assertFalse(self.observer.pending)

    def test_model_request_report_declaration_and_mentions_are_separate(self) -> None:
        self.observer.returned("call", self.catalog.detail("c1"))
        turn = ModelTurn("提到 c1，不是 c10；引用 [app.py:1] 不证明使用了哪条路径。", "fixture", "fixture", (
            ToolCall("get", "get_static_security_evidence", {"evidence_id": "c2", "section": "flow"}),
            ToolCall("report", "submit_analysis_batch", {"evidence_ids": ["c2"], "ledger_json": LEDGER_JSON}),
        ), STOP)
        self.observer.model_observed(turn)
        payload = self.events[-1][1]
        self.assertEqual(["c1"], payload["explicit_id_mentions"])
        self.assertEqual(["c2"], payload["declared_report_ids"])
        self.assertEqual("c2", payload["requested"][0]["evidence_id"])
        self.assertFalse(any(kind == "paths.send_attempt" for kind, _ in self.events))

    def test_malformed_report_call_is_recorded_without_hiding_original_protocol_error(self) -> None:
        turn = ModelTurn("", "fixture", "fixture", (ToolCall("bad", "submit_analysis_batch", {"evidence_ids": None}),), STOP)
        self.observer.model_observed(turn)
        self.assertEqual([], self.events[-1][1]["declared_report_ids"])
        with self.assertRaises(ModelOutputError) as caught:
            AgentRunManager._require_answer(turn, forbid_tools=True)
        self.assertEqual("unexpected_tool_calls", caught.exception.code)


def long_ledger() -> str:
    """构建字段合法、JSON 与生成正文均超过旧上限的台账，不靠空格填充。"""
    value = json.loads(LEDGER_JSON)
    value["unreviewed"] = [f"样例范围 {index}：" + "此范围未核实，不能据此推断安全。" * 80 for index in range(25)]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class UnboundedBatchReportTests(unittest.TestCase):
    """移除 JSON/生成正文/工具 Schema 的总字符上限，保留传输完整性和台账契约。"""

    def test_previously_rejected_16197_character_ledger_is_accepted(self) -> None:
        padded = LEDGER_JSON + " " * (16197 - len(LEDGER_JSON))
        state = AnalysisBatches(metadata=STOP, scopes={"app.py:1-2"})
        state.publish(padded, [], ["app.py:1-2"])
        self.assertEqual(1, len(state.reports))
        self.assertEqual(json.loads(LEDGER_JSON), state.reports[0]["ledger"])

    def test_long_ledger_and_rendered_report_are_not_truncated(self) -> None:
        store = MemoryRunStore()
        manager = AgentRunManager(store=store)
        with tempfile.TemporaryDirectory() as directory:
            context = ToolContext(project_id="p", user_id="u", project_root=Path(directory), artifact={})
            context.batches.metadata = STOP
            context.batches.scopes.add("app.py:1-2")
            text = long_ledger()
            self.assertGreater(len(text), 16000)
            manager._publish_batch("run", context, text)
        completed = next(payload for kind, payload in store.events if kind == "batch.completed")
        self.assertGreater(len(completed["report"]), 16000)
        self.assertEqual(json.loads(text), completed["ledger"])
        self.assertTrue(completed["report"].endswith("<!-- SECURITY_REPORT_END -->"))
        self.assertFalse(any(kind == "batch.report_rejected" for kind, _ in store.events))

    def test_submission_schema_and_validation_do_not_limit_total_characters(self) -> None:
        text = long_ledger()
        arguments = BatchReportArguments(ledger_json=text, scopes=["app.py:1-2"])
        self.assertEqual(text, arguments.ledger_json)
        schema = BatchReportArguments.model_json_schema()["properties"]["ledger_json"]
        self.assertNotIn("maxLength", schema)
        self.assertEqual(1, schema["minLength"])

    def test_unlimited_size_does_not_accept_incomplete_or_invalid_reports(self) -> None:
        for metadata, text in ((ModelResponseMetadata(finish_reason="length"), long_ledger()),
                               (STOP, long_ledger()[:-1])):
            with self.subTest(finish_reason=metadata.finish_reason):
                state = AnalysisBatches(metadata=metadata, scopes={"app.py:1-2"})
                with self.assertRaises(ValueError):
                    state.publish(text, [], ["app.py:1-2"])
                self.assertEqual([], state.reports)
                self.assertEqual({"app.py:1-2"}, state.scopes)

    def test_policy_records_unlimited_size_but_keeps_report_mode_and_rounds(self) -> None:
        policy = execution_policy(12, "tools_none", batch_mode=True)
        self.assertIn("batch_report_max_chars", policy)
        self.assertIsNone(policy["batch_report_max_chars"])
        self.assertEqual("isolated_text_no_tools", policy["final_report_mode"])
        self.assertEqual("tools_none", policy["configured_final_report_mode"])
        self.assertEqual(12, policy["max_tool_rounds"])
        self.assertNotIn("report_limit", json.loads(AnalysisBatches().message()["content"].split("\n", 1)[1]))


class GenerationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """规划请求/模拟 Provider 不冒充 HTTP；观察器不干预真实请求参数或回答。"""

    async def test_provider_without_transport_callback_does_not_invent_delivery(self) -> None:
        store = MemoryRunStore()
        manager = AgentRunManager(store=store)
        provider = Mock(name="provider")
        provider.name, provider.model = "fixture", "fixture"
        provider.generate_with_tools = AsyncMock(return_value=ModelTurn(LEDGER_JSON, "fixture", "fixture", metadata=STOP))
        page = SecurityEvidenceCatalog(fixture_pack().model_dump()).detail("c1")
        messages = [{"role": "tool", "content": json.dumps({"content": page})}]
        await manager._generate_once("run", provider, {"messages": messages}, 2, messages=messages, tools=[], instructions="test")
        self.assertTrue(any(kind == "model.completed" for kind, _ in store.events))
        self.assertFalse(any(kind == "paths.send_attempt" for kind, _ in store.events))

    async def test_generation_wires_observer_and_restores_context(self) -> None:
        store = MemoryRunStore()
        manager = AgentRunManager(store=store)
        provider = Mock(name="provider")
        provider.name, provider.model = "fixture", "fixture"
        page = SecurityEvidenceCatalog(fixture_pack().model_dump()).detail("c1", section="flow", limit=2)
        messages = [{"role": "tool", "content": json.dumps({"content": page})}]

        async def simulated_transport(**kwargs):
            observer = WIRE_OBSERVER.get()
            self.assertIsNotNone(observer)
            request_id = observer.sent({"messages": kwargs["messages"]}, 1)
            observer.received(request_id, {})
            return ModelTurn(LEDGER_JSON, "fixture", "fixture", metadata=STOP)

        provider.generate_with_tools = simulated_transport
        before = WIRE_OBSERVER.get()
        await manager._generate_once("run", provider, {"messages": messages}, 2, messages=messages, tools=[], instructions="test")
        self.assertIs(before, WIRE_OBSERVER.get())
        sent = [payload for kind, payload in store.events if kind == "paths.send_attempt"]
        self.assertEqual(1, len(sent))
        self.assertEqual(["c1"], [ref["id"] for ref in sent[0]["references"]])
        self.assertEqual([0, 1], sent[0]["references"][0]["item_orders"])


class SourceRetentionTests(unittest.TestCase):
    """只对选定的 CLI/环境输入验收，不将未发现路径推广为全语言或全项目结论。"""

    def test_established_flows_beyond_a_single_endpoint_window_reach_the_catalog(self) -> None:
        source = "import os\ndef first():\n value=input()\n os.system(value)\ndef later():\n value=input()\n os.system(value)\n"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'app.py').write_text(source,encoding='utf8')
            analysis=UnifiedCodeAnalyzer(directory,max_workers=1,semantic_resolver=NonNativeResolver()).run_full_analysis()
            pack=SecurityAnalysisService(max_sources=1,max_sinks=1).analyze(project_root=directory,
                dependency_graph=analysis['dependency_graph'],semantic_index=analysis['semantic_index'])
        routes=[item for item in pack.candidates if item.dataflow_verified]
        self.assertEqual(2,len(routes))
        self.assertEqual({'app.py::first','app.py::later'}, {pack.facts[item.source_fact_id].symbol for item in routes})
        catalog=SecurityEvidenceCatalog(pack.model_dump())
        for route in routes:
            steps=catalog.detail(route.candidate_id,section='flow')['items']
            self.assertTrue(steps[-1]['argument_bindings'])
            self.assertEqual('sink',steps[-1]['kind'])

    def test_admin_cli_and_environment_sources_remain_with_unknown_permissions(self) -> None:
        source = """import os
import subprocess
def administrator_cli():
    value = input('administrator command: ')
    return subprocess.run(value, shell=True)
def configured_command():
    value = os.getenv('RUN_COMMAND')
    return subprocess.run(value, shell=True)
"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(source, encoding="utf8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1, semantic_resolver=NonNativeResolver()).run_full_analysis()
            for limit in (1, 3):
                with self.subTest(source_pairing_budget=limit):
                    pack = SecurityAnalysisService(max_sources=limit).analyze(project_root=directory,
                        dependency_graph=analysis["dependency_graph"], semantic_index=analysis["semantic_index"])
                    sources = {fact.category: fact for fact in pack.facts.values() if fact.fact_kind == "source"}
                    self.assertEqual({"cli_input", "environment_read"}, set(sources))
                    self.assertTrue(all(fact.metadata["permission_requirement"] == "unknown" for fact in sources.values()))
                    if limit == 3:
                        self.assertEqual(set(fact.fact_id for fact in sources.values()),
                                         {item.source_fact_id for item in pack.candidates if item.dataflow_verified})
                        catalog = SecurityEvidenceCatalog(pack.model_dump())
                        for item in catalog.selector.rows():
                            self.assertEqual("unknown", item["source"]["permission_requirement"])
                            self.assertEqual(pack.facts[item["source"]["id"]].category, item["source"]["source_kind"])


if __name__ == "__main__":
    unittest.main()
