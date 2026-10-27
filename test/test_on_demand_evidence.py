"""按需目录、ID 详情及两组分批会话的离线回归；不调用真实模型。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.security_analysis.contracts import (
    SecurityEvidencePack, StaticSecurityFact, SecurityCandidate, EvidenceLocation,
    CodeSnippet, DataFlowEvidence, FlowStepEvidence,
)
from backend.app.services.security_analysis.evidence_query import SecurityEvidenceCatalog
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector
from backend.app.agents.batching import AnalysisBatches
from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.tools import create_project_tool_registry
from backend.app.agents.tools.base import ToolContext
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.conversation_window import ToolRound, round_messages
from backend.app.agents.reporting import phase_tools, phase_message
from backend.app.llm.conversation import validate_messages, response_items
from backend.app.experiments.context import SecurityExperimentContextBuilder, prepare_experiment_artifact, SECURITY_EXPERIMENT_INSTRUCTIONS
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.llm.base import ModelProvider, ModelResult, ModelTurn, ToolCall
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.llm.settings import ModelLimits
from backend.app.experiments.citations import visible_lines
from test.test_agent_foundation import MemoryRunStore
from backend.app.experiments.findings import COVERAGE_LABELS, LEDGER_VERSION, parse_ledger

def ledger_text(path='app.py', start=1, end=2):
    return json.dumps({'schema_version':LEDGER_VERSION,'findings':[],
        'coverage':{key:{'status':'not_checked','note':'仅完成本批定位，未充分核对本类别。','evidence':[]} for key in COVERAGE_LABELS},
        'reviewed_ranges':[{'path':path,'start_line':start,'end_line':end}],
        'unreviewed':['范围之外未检查。'],'limitations':['纯静态样例；语义正确性未证明。']}, ensure_ascii=False)


LEDGER_JSON = ledger_text()
REPORT = parse_ledger(LEDGER_JSON).render()
STOP = ModelResponseMetadata(finish_reason="stop", usage=ModelUsage(100, 40, 140))
TOOLS = ModelResponseMetadata(finish_reason="tool_calls", usage=ModelUsage(100, 40, 140))


def fixture_pack():
    pack = SecurityEvidencePack()
    for index in (1, 2):
        source = StaticSecurityFact(fact_id=f"s{index}", fact_kind="source", rule_id="stdin", category="stdin",
            name="input", symbol="app.py::run", location=EvidenceLocation(path="app.py", line=index * 2 - 1), trust_class="untrusted")
        sink = StaticSecurityFact(fact_id=f"k{index}", fact_kind="sink", rule_id="eval", category="code_execution",
            name="eval", symbol="app.py::run", location=EvidenceLocation(path="app.py", line=index * 2), severity="high")
        pack.facts.update({source.fact_id: source, sink.fact_id: sink})
        snippet = CodeSnippet(snippet_id=f"n{index}", role="sink", path="app.py", start_line=index * 2 - 1,
            end_line=index * 2, text="value = input()\neval(value)")
        pack.snippets[snippet.snippet_id] = snippet
        flow = DataFlowEvidence(flow_id=f"f{index}", language="python", symbol=source.symbol,
            source_fact_id=source.fact_id, sink_fact_id=sink.fact_id, status="may_reach_sink", confidence="medium",
            steps=[FlowStepEvidence(step_id=f"step{offset}", order=offset, kind="assignment", symbol=source.symbol,
                location=source.location, expression=f"v{offset} = value") for offset in range(15)])
        pack.dataflows[flow.flow_id] = flow
        pack.candidates.append(SecurityCandidate(candidate_id=f"c{index}", rule_id="fixture", category="code_execution",
            severity="high", confidence="medium", source_fact_id=source.fact_id, sink_fact_id=sink.fact_id,
            path_kind="intra_procedural_dataflow", dataflow_verified=True, dataflow_id=flow.flow_id, snippet_ids=[snippet.snippet_id]))
    return pack


class EvidenceQueryTests(unittest.TestCase):
    def test_initial_directory_has_no_bodies_and_excludes_reported_ids(self):
        catalog = SecurityEvidenceCatalog(fixture_pack().model_dump())
        summary = json.loads(catalog.summary(excluded={"c1"}))
        self.assertEqual(1,len(summary['items']))
        self.assertEqual(['c2'],[row['id'] for row in catalog.detail(summary['items'][0]['id'])['items']])
        self.assertNotIn("snippets", summary)
        self.assertNotIn("value = input()", json.dumps(summary))
        self.assertIn("untrusted", json.dumps(summary))

    def test_summary_search_and_direct_selection_share_order_ids_and_priority(self):
        raw=fixture_pack().model_dump();catalog=SecurityEvidenceCatalog(raw)
        summary=json.loads(catalog.summary())
        direct=SecurityEvidenceSelector(raw).build()
        self.assertEqual(direct,summary)
        rows=catalog.search()['items']
        self.assertEqual([row['id'] for row in rows[:len(summary['items'])]],[row['id'] for row in summary['items']])
        for row in summary['items']:
            overview=catalog.detail(row['id'])['group']
            for key in ('id','priority','kind','relation','source','sink'):
                self.assertEqual(row[key],overview[key])
        for key in ('coverage','limitations','rule_packs','entrypoints','snippets'):
            self.assertNotIn(key,summary)
        self.assertEqual({},visible_lines({'messages':[{'role':'user','content':'q\n\nTRUSTED_STATIC_CONTEXT\nSTATIC_SECURITY_EVIDENCE\n'+catalog.summary()}]}))

    def test_missing_endpoint_lead_is_addressable_and_global_coverage_remains_separate(self):
        pack=fixture_pack();pack.facts.pop('s1')
        catalog=SecurityEvidenceCatalog(pack.model_dump())
        summary=json.loads(catalog.summary())
        self.assertEqual(1,summary['unavailable_candidates'])
        self.assertEqual('operation_lead',catalog.detail('k1')['items'][0]['kind'])
        self.assertIn('coverage',catalog.detail('_coverage')['items'][0])

    def test_unresolved_pages_retain_candidate_and_flow_limitations(self):
        pack=fixture_pack();pack.candidates[0].limitations=['shared','candidate only']
        pack.dataflows['f1'].unresolved=['shared','flow only']
        detail=SecurityEvidenceCatalog(pack.model_dump()).detail('c1',section='unresolved')
        self.assertEqual(['shared','candidate only','flow only'],[item['description'] for item in detail['items']])
        self.assertEqual(3,detail['section_counts']['unresolved'])

    def test_cursor_filters_snapshot_and_changed_completed_set(self):
        catalog = SecurityEvidenceCatalog(fixture_pack().model_dump())
        first = catalog.search(limit=1)
        second = catalog.search(cursor=first["next_cursor"], limit=1)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertFalse(second["has_more"])
        with self.assertRaises(ValueError):catalog.search(cursor=first["next_cursor"], excluded={"c1"})
        with self.assertRaises(ValueError):catalog.search(cursor="not-a-cursor")
        self.assertEqual(0, catalog.search(query="absent")['total_matching'])
        self.assertEqual(2, catalog.search(trust_class="untrusted")['total_matching'])
        with self.assertRaises(ValueError):catalog.detail("other-project-id")

    def test_all_path_steps_and_snippets_are_addressable_without_sampling(self):
        catalog = SecurityEvidenceCatalog(fixture_pack().model_dump())
        orders, offset = [], 0
        while True:
            page = catalog.detail("c1", section="flow", offset=offset, limit=4)
            orders.extend(item["order"] for item in page["items"])
            offset = page["next_offset"]
            if not page["has_more"]:break
        self.assertEqual(list(range(15)), orders)
        snippets = catalog.detail("c1", section="snippets")
        self.assertEqual("value = input()\neval(value)", snippets['snippets']['n1']['text'])
        self.assertEqual(15, catalog.detail("c1")['section_counts']['flow'])
        self.assertNotIn("flow", catalog.detail("c1")['items'][0])

    def test_omitted_global_limitations_remain_accessible_by_id(self):
        pack = fixture_pack()
        pack.limitations = [f"limitation-{index}" for index in range(20)]
        pack.scan_failures = [{'path':'bad.py','reason':'parse_error','detail':'private internal detail'}]
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        self.assertEqual('_coverage', json.loads(catalog.summary())['coverage_details_id'])
        first = catalog.detail('_coverage', section='unresolved', limit=10)
        second = catalog.detail('_coverage', section='unresolved', offset=first['next_offset'], limit=10)
        third = catalog.detail('_coverage', section='unresolved', offset=second['next_offset'], limit=10)
        self.assertEqual(21, len(first['items'])+len(second['items'])+len(third['items']))
        self.assertNotIn('private internal detail', json.dumps(third))

    def test_failed_batch_does_not_consume_ids_and_same_file_new_range_is_allowed(self):
        state = AnalysisBatches()
        state.select("c1")
        with self.assertRaises(ValueError):state.publish(LEDGER_JSON, ["c1"], [])
        state.observe([{"role": "tool", "content": json.dumps({"content": {"view": "detail", "id": "c1", "items": [{}]}})}])
        state.metadata = ModelResponseMetadata(finish_reason="length")
        with self.assertRaises(ValueError):state.publish(LEDGER_JSON, ["c1"], [])
        self.assertEqual(set(), state.completed_ids)
        state.metadata = TOOLS
        state.publish(LEDGER_JSON, ["c1"], [])
        with self.assertRaises(ValueError):state.select("c1")
        for start in (1, 3):
            state.observe([{"role": "tool", "content": json.dumps({"content": {"path": "app.py", "start_line": start, "end_line": start + 1, "text": "observed"}})}])
            state.publish(LEDGER_JSON, [], [f"app.py:{start}-{start+1}"])
        self.assertEqual(3, len(state.reports))

    def test_final_archive_contains_only_shown_frames_and_mechanical_source_ranges(self):
        state = AnalysisBatches(metadata=STOP)
        frame = {"content": {"path": "app.py", "start_line": 1, "end_line": 2, "text": "1: input()\n2: eval(value)"}}
        state.observe([{"role": "tool", "content": json.dumps(frame)}])
        state.publish(LEDGER_JSON, [], ['app.py:1-2'])
        message = state.evidence_message()
        self.assertEqual({'app.py':{1,2}}, visible_lines({'messages':[message]}))
        omitted = json.loads(state.evidence_message(0)['content'].split('\n',1)[1])
        self.assertEqual([], omitted['observations'])
        self.assertEqual(1, omitted['omitted_observations'])
        self.assertNotIn('eval(value)', json.dumps(omitted))


class BatchModel(ModelProvider):
    name = "fixture"
    model = "fixture"
    model_limits = ModelLimits(0, 2400, max_input_tokens=100000)

    def __init__(self, augmented, fail_after_first=False):
        self.augmented, self.fail_after_first = augmented, fail_after_first
        self.calls = []
        self.options = []
        self.investigations = 0
        self.batch_reports = 0

    async def generate(self, *, instructions, prompt):
        return ModelResult(LEDGER_JSON, self.name, self.model, STOP)

    async def generate_with_tools(self, *, instructions, messages, tools, **kwargs):
        self.calls.append(messages)
        self.options.append((tools, kwargs.get('tool_choice', 'auto')))
        names = {item['name'] for item in tools}
        if names == {'submit_analysis_batch'}:
            self.batch_reports += 1
            if self.fail_after_first and self.batch_reports == 2:
                return ModelTurn("", self.name, self.model, metadata=ModelResponseMetadata(finish_reason="length"))
            state = json.loads(next(item['content'] for item in messages if item.get('content','').startswith('APPLICATION_BATCH_STATE\n')).split('\n',1)[1])
            arguments = {"ledger_json": LEDGER_JSON, "evidence_ids": state['active_ids'], "scopes": state['shown_scopes']}
            return ModelTurn("", self.name, self.model, (ToolCall("submit", "submit_analysis_batch", arguments),), TOOLS)
        if not tools:
            return ModelTurn(LEDGER_JSON, self.name, self.model, metadata=STOP)
        self.investigations += 1
        if self.investigations in (1, 3):
            index = 1 if self.investigations == 1 else 2
            calls = [ToolCall("overview", "get_static_security_evidence", {"evidence_id": f"c{index}", "section": "overview", "offset": 0, "limit": 5}),
                     ToolCall("snippet", "get_static_security_evidence", {"evidence_id": f"c{index}", "section": "snippets", "offset": 0, "limit": 5})] if self.augmented else [
                     ToolCall("raw", "read_file_range", {"path": "app.py", "start_line": index * 2 - 1, "end_line": index * 2})]
            return ModelTurn("", self.name, self.model, tuple(calls), TOOLS)
        return ModelTurn('本批证据已取得，可以进入独立报告阶段。', self.name, self.model, metadata=STOP)


class ExhaustedBatchModel(BatchModel):
    """首轮读取后耗尽调查预算，覆盖强制本批报告及独立汇总分支。"""

    def __init__(self, augmented, report_metadata=STOP, *, directory_only=False):
        super().__init__(augmented)
        self.report_metadata = report_metadata
        self.directory_only = directory_only
        self.report_options = []

    async def generate_with_tools(self, *, instructions, messages, tools, **kwargs):
        if not self.calls:
            turn = await super().generate_with_tools(instructions=instructions, messages=messages, tools=tools, **kwargs)
            if self.directory_only:
                return ModelTurn('', self.name, self.model, (ToolCall('files', 'list_project_files', {}),), TOOLS)
            return turn
        self.calls.append(messages)
        self.options.append((tools, kwargs.get('tool_choice', 'auto')))
        self.report_options.append((tools, kwargs.get('tool_choice')))
        metadata = self.report_metadata if len(self.calls) == 2 else STOP
        return ModelTurn(LEDGER_JSON, self.name, self.model, metadata=metadata)


class BatchConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_agent_empty_id_search_uses_same_catalog_and_never_claims_source_read(self):
        raw = fixture_pack().model_dump()
        with tempfile.TemporaryDirectory() as directory:
            context = ToolContext(project_id='p',user_id='u',project_root=Path(directory),artifact={'security_evidence':raw})
            registry = create_project_tool_registry()
            first = await registry.execute('get_static_security_evidence', context, {'query':'', 'limit':1})
            self.assertEqual(SecurityEvidenceCatalog(raw).search(limit=1), first.content)
            self.assertEqual([], first.evidence)
            second = await registry.execute('get_static_security_evidence', context, {'cursor':first.content['next_cursor'], 'limit':1})
            self.assertNotEqual(first.content['items'][0]['id'], second.content['items'][0]['id'])
            self.assertNotIn('findings', first.content)
            with self.assertRaises(ValueError):
                await registry.execute('get_static_security_evidence', context, {'offset':1})

    async def test_normal_agent_can_read_initial_ids_without_batch_limit_or_new_tools(self):
        pack=fixture_pack().model_dump()
        with tempfile.TemporaryDirectory() as directory:
            context=ToolContext(project_id='p',user_id='u',project_root=Path(directory),artifact={'security_evidence':pack})
            registry=create_project_tool_registry()
            for identifier in ('c1','c2','_coverage','c1'):
                result=await registry.execute('get_static_security_evidence',context,{'evidence_id':identifier,'section':'overview'})
                self.assertTrue(result.content['items'])
            self.assertEqual(set(),context.batches.active_ids)

    async def run_model(self, augmented, fail=False, *, provider=None, max_steps=4, final_mode='tools_none'):
        provider = provider or BatchModel(augmented, fail)
        store = MemoryRunStore()
        manager = AgentRunManager(store=store, tools=create_experiment_tool_registry(with_evidence=augmented),
            context_builder=SecurityExperimentContextBuilder(with_evidence=augmented), instructions=SECURITY_EXPERIMENT_INSTRUCTIONS)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text("a = input()\neval(a)\nb = input()\neval(b)\n", encoding="utf-8")
            with patch('backend.app.agents.orchestrator.create_model_provider', return_value=provider), \
                    patch('backend.app.experiments.context.get_model_limits', return_value=provider.model_limits), \
                    patch('backend.app.agents.orchestrator.get_final_report_mode', return_value=final_mode):
                await manager._run(run_id="run", project_id="project", user_id="user", project_root=directory,
                    artifact=prepare_experiment_artifact({"security_evidence": fixture_pack().model_dump()}, with_evidence=augmented),
                    request=AgentRunRequest(question="分析代码安全", max_steps=max_steps))
        return provider, store

    async def test_exhausted_rounds_finish_open_batch_then_synthesize_in_both_report_modes(self):
        for augmented in (True, False):
            for mode in ('tools_none', 'history_no_tools'):
                with self.subTest(augmented=augmented, final_mode=mode):
                    provider = ExhaustedBatchModel(augmented)
                    _, store = await self.run_model(augmented, provider=provider, max_steps=1, final_mode=mode)
                    self.assertEqual('completed', store.status)
                    starts = [item for name,item in store.events if name == 'model.started']
                    self.assertEqual(['investigation', 'batch_report', 'final_report'], [item['phase'] for item in starts])
                    self.assertEqual(3, len(provider.calls))
                    batches = [item for name,item in store.events if name == 'batch.completed']
                    self.assertEqual(1, len(batches))
                    self.assertEqual(['c1'] if augmented else [], batches[0]['evidence_ids'])
                    self.assertEqual('stop', batches[0]['metadata']['finish_reason'])
                    self.assertFalse(any(item['role'] == 'tool' or item.get('tool_calls') or item.get('provider_items') for item in provider.calls[1]))
                    self.assertTrue(any(item.get('content','').startswith('APPLICATION_BATCH_EVIDENCE\n') for item in provider.calls[1]))
                    self.assertIn('当前仅输出本批完整发现台账 JSON', provider.calls[1][-1]['content'])
                    self.assertFalse(any(item['role'] == 'tool' for item in provider.calls[-1]))
                    self.assertFalse(any(name == 'batch.pending' for name,_ in store.events))
                    self.assertEqual({'submit_analysis_batch'}, {item['name'] for item in provider.report_options[0][0]})
                    self.assertEqual('auto', provider.report_options[0][1])
                    self.assertEqual(([], 'none'), provider.report_options[-1])

    async def test_incomplete_forced_report_does_not_publish_or_start_final_synthesis(self):
        for augmented in (True, False):
            with self.subTest(augmented=augmented):
                provider = ExhaustedBatchModel(augmented, ModelResponseMetadata(finish_reason='length'))
                _, store = await self.run_model(augmented, provider=provider, max_steps=1)
                self.assertEqual('failed', store.status)
                self.assertEqual(2, len(provider.calls))
                self.assertFalse(any(name == 'batch.completed' for name,_ in store.events))
                pending = [item for name,item in store.events if name == 'batch.pending']
                self.assertEqual('unreported', pending[-1]['status'])
                failure = [item for name,item in store.events if name == 'run.failed'][-1]
                self.assertEqual('ModelOutputError', failure['error_type'])
                self.assertEqual('length', failure['final_response_metadata']['finish_reason'])

    async def test_directory_only_finalization_retains_history_without_inventing_analyzed_scope(self):
        provider = ExhaustedBatchModel(False, directory_only=True)
        _, store = await self.run_model(False, provider=provider, max_steps=1)
        self.assertEqual('completed', store.status)
        starts = [item for name,item in store.events if name == 'model.started']
        self.assertEqual(['investigation', 'final_report'], [item['phase'] for item in starts])
        self.assertFalse(any(item['role'] == 'tool' for item in provider.calls[-1]))
        self.assertTrue(any(item.get('content','').startswith('APPLICATION_BATCH_EVIDENCE\n') for item in provider.calls[-1]))
        self.assertFalse(any(name == 'batch.completed' for name,_ in store.events))

    async def test_both_groups_publish_before_next_batch_and_replay_only_ledger_until_final(self):
        for augmented in (True, False):
            with self.subTest(augmented=augmented):
                provider, store = await self.run_model(augmented)
                self.assertEqual("completed", store.status)
                batches = [payload for name,payload in store.events if name == 'batch.completed']
                self.assertEqual([1,2], [item['batch_id'] for item in batches])
                self.assertEqual(7, len(provider.calls))
                self.assertFalse(any(item['role']=='tool' for item in provider.calls[3]))
                ledger = json.loads(provider.calls[3][-1]['content'].split('\n',1)[1])
                self.assertEqual(json.loads(LEDGER_JSON), ledger['reports'][0]['ledger'])
                self.assertNotIn('report', ledger['reports'][0])
                self.assertFalse(any(item['role']=='tool' for item in provider.calls[-1]))
                self.assertTrue(any(item['content'].startswith('APPLICATION_BATCH_EVIDENCE\n') for item in provider.calls[-1]))
                if augmented:
                    directory = json.loads(provider.calls[3][0]['content'].split('STATIC_SECURITY_EVIDENCE\n',1)[1])
                    self.assertEqual(1,len(directory['items']))
                    self.assertEqual('s2',directory['items'][0]['source']['id'])
                    self.assertEqual(['c1'], batches[0]['evidence_ids'])
                else:
                    self.assertNotIn('STATIC_SECURITY_EVIDENCE', provider.calls[0][0]['content'])

    async def test_failure_preserves_prior_batch_and_leaves_next_id_unreported(self):
        provider, store = await self.run_model(True, fail=True)
        self.assertEqual('failed', store.status)
        batches = [item for name,item in store.events if name=='batch.completed']
        pending = [item for name,item in store.events if name=='batch.pending']
        self.assertEqual(1, len(batches))
        self.assertEqual(['c2'], pending[-1]['evidence_ids'])

    async def test_one_investigation_round_still_allows_tool_report_and_final_summary(self):
        for augmented in (False, True):
            with self.subTest(augmented=augmented):
                provider = BatchModel(augmented)
                _, store = await self.run_model(augmented, provider=provider, max_steps=1)
                self.assertEqual('completed', store.status)
                starts = [payload for kind,payload in store.events if kind == 'model.started']
                self.assertEqual(['investigation','batch_report','final_report'], [row['phase'] for row in starts])
                self.assertEqual([1,2,3], [row['step'] for row in starts])
                self.assertNotIn('submit_analysis_batch', {row['name'] for row in provider.options[0][0]})
                self.assertEqual({'submit_analysis_batch'}, {row['name'] for row in provider.options[1][0]})
                self.assertEqual([], provider.options[2][0])
                batch = next(payload for kind,payload in store.events if kind == 'batch.completed')
                self.assertEqual('tool_calls', batch['metadata']['finish_reason'])
                self.assertEqual(['app.py:1-2'] if not augmented else [], batch['scopes'])

    async def test_reports_do_not_reduce_investigation_rounds_and_steps_remain_unique(self):
        provider, store = await self.run_model(False, max_steps=4)
        starts = [payload for kind,payload in store.events if kind == 'model.started']
        self.assertEqual(4, sum(row['phase']=='investigation' for row in starts))
        self.assertEqual(2, sum(row['phase']=='batch_report' for row in starts))
        self.assertEqual(1, sum(row['phase']=='final_report' for row in starts))
        self.assertEqual(list(range(1,8)), [row['step'] for row in starts])
        self.assertEqual([4,3,2,1], [row['remaining_tool_rounds'] for row in starts if row['phase']=='investigation'])
        self.assertTrue(all(not any(message['role']=='tool' or message.get('tool_calls') or message.get('provider_items')
                                    for message in provider.calls[index])
                            for index, row in enumerate(starts) if row['phase']!='investigation'))

    async def test_report_stage_does_not_execute_investigation_or_mixed_calls(self):
        for mixed in (False, True):
            class InvalidReportModel(BatchModel):
                async def generate_with_tools(inner, **kwargs):
                    turn = await super().generate_with_tools(**kwargs)
                    if {row['name'] for row in kwargs['tools']} == {'submit_analysis_batch'}:
                        calls = (*turn.tool_calls, ToolCall('forbidden', 'read_file_range', {'path':'app.py','start_line':1,'end_line':2})) if mixed else (
                            ToolCall('forbidden', 'read_file_range', {'path':'app.py','start_line':1,'end_line':2}),)
                        return ModelTurn('', inner.name, inner.model, calls, TOOLS)
                    return turn
            with self.subTest(mixed=mixed):
                _, store = await self.run_model(False, provider=InvalidReportModel(False), max_steps=1)
                failure = next(payload for kind,payload in store.events if kind=='run.failed')
                self.assertEqual('unexpected_report_tool_calls', failure['error_code'])
                self.assertEqual(1, sum(kind=='tool.completed' and payload['name']=='read_file_range' for kind,payload in store.events))
                self.assertFalse(any(kind=='batch.completed' for kind,_ in store.events))

    async def test_report_tool_wrong_fields_are_not_silently_renamed(self):
        class InvalidArgumentsModel(BatchModel):
            async def generate_with_tools(inner, **kwargs):
                turn = await super().generate_with_tools(**kwargs)
                if {row['name'] for row in kwargs['tools']} == {'submit_analysis_batch'}:
                    arguments = {**turn.tool_calls[0].arguments}
                    arguments['batch'] = json.loads(arguments.pop('ledger_json'))
                    return ModelTurn('', inner.name, inner.model, (ToolCall('submit', 'submit_analysis_batch', arguments),), TOOLS)
                return turn
        _, store = await self.run_model(False, provider=InvalidArgumentsModel(False), max_steps=1)
        failure = next(payload for kind,payload in store.events if kind=='run.failed')
        self.assertEqual('invalid_batch_submission', failure['error_code'])
        self.assertFalse(any(kind=='batch.completed' for kind,_ in store.events))

    async def test_hidden_report_tool_cannot_execute_during_investigation(self):
        class HiddenReportModel(BatchModel):
            async def generate_with_tools(inner, **kwargs):
                turn = await super().generate_with_tools(**kwargs)
                if inner.investigations == 1 and {row['name'] for row in kwargs['tools']} != {'submit_analysis_batch'}:
                    return ModelTurn('', inner.name, inner.model, (ToolCall('hidden', 'submit_analysis_batch', {
                        'ledger_json': LEDGER_JSON, 'evidence_ids':[], 'scopes':[]}),), TOOLS)
                return turn
        _, store = await self.run_model(False, provider=HiddenReportModel(False), max_steps=1)
        failures = [payload for kind,payload in store.events if kind=='tool.failed']
        self.assertEqual('submit_analysis_batch', failures[0]['name'])
        self.assertIn('允许列表', failures[0]['error'])
        self.assertFalse(any(kind=='tool.completed' and payload['name']=='submit_analysis_batch' for kind,payload in store.events))
        self.assertFalse(any(kind=='batch.completed' for kind,_ in store.events))

    async def test_report_format_retry_has_no_investigation_tools_or_round_cost(self):
        class MalformedReportModel(BatchModel):
            async def generate_with_tools(inner, **kwargs):
                turn = await super().generate_with_tools(**kwargs)
                if {row['name'] for row in kwargs['tools']} == {'submit_analysis_batch'} and inner.batch_reports == 1:
                    return ModelTurn(LEDGER_JSON[:-1], inner.name, inner.model, metadata=STOP)
                return turn
        provider, store = await self.run_model(False, provider=MalformedReportModel(False), max_steps=1)
        self.assertEqual('completed', store.status)
        starts = [payload for kind,payload in store.events if kind=='model.started']
        self.assertEqual(['investigation','batch_report','report_format_retry','final_report'], [row['phase'] for row in starts])
        self.assertEqual({'submit_analysis_batch'}, {row['name'] for row in provider.options[2][0]})
        self.assertEqual('auto', provider.options[2][1])
        self.assertEqual(1, provider.investigations)
        self.assertEqual(1, sum(kind=='batch.completed' for kind,_ in store.events))


class ReportProjectionTests(unittest.TestCase):
    def test_projection_preserves_source_but_drops_native_calls_definitions_and_hidden_state(self):
        result = {'content':{'path':'app.py','start_line':1,'end_line':1,'text':'1: read_file_range = input()'}}
        round_ = ToolRound({'role':'assistant','content':'调查草稿不是事实','tool_calls':[{'id':'call','type':'function',
            'function':{'name':'read_file_range','arguments':'{}'}}], 'reasoning_content':'PRIVATE_REASONING',
            'provider_items':[{'type':'function_call','name':'OLD_TOOL','arguments':'{}'}]},
            [{'call_id':'call','name':'read_file_range','arguments':{'path':'app.py'},'result':result}])
        messages = round_messages(round_, report_only=True)
        validate_messages(messages)
        self.assertEqual(['user'], [message['role'] for message in messages])
        envelope = json.loads(messages[0]['content'].split('\n',1)[1])
        self.assertEqual({'observation_id':'call','result':result}, envelope['observations'][0])
        self.assertEqual({'app.py':{1}}, visible_lines({'messages':messages}))
        self.assertTrue(all(item.get('type') not in {'function_call','function_call_output','reasoning'} for item in response_items(messages)))
        self.assertNotIn('PRIVATE_REASONING', json.dumps(messages))
        self.assertNotIn('OLD_TOOL', json.dumps(messages))
        self.assertNotIn('调查草稿不是事实', json.dumps(messages, ensure_ascii=False))

    def test_state_preview_does_not_mark_unsent_tool_results_as_delivered(self):
        state = AnalysisBatches()
        frame = {'role':'user','content':'APPLICATION_BATCH_EVIDENCE\n'+json.dumps({'observations':[{'result':{
            'content':{'path':'app.py','start_line':1,'end_line':2,'text':'1: value=input()\n2: eval(value)'}}}]})}
        preview = json.loads(state.message([frame])['content'].split('\n',1)[1])
        self.assertEqual(['app.py:1-2'], preview['shown_scopes'])
        self.assertEqual(set(), state.scopes)
        self.assertEqual({}, state.frames)
        state.observe([frame])
        self.assertEqual({'app.py:1-2'}, state.scopes)

    def test_stage_tool_selection_and_closing_notice_are_explicit(self):
        schemas = create_experiment_tool_registry(with_evidence=True).schemas()
        self.assertNotIn('submit_analysis_batch', {row['name'] for row in phase_tools(schemas,'investigation')})
        self.assertEqual(['submit_analysis_batch'], [row['name'] for row in phase_tools(schemas,'batch_report')])
        self.assertEqual([], phase_tools(schemas,'final_report'))
        self.assertEqual(7, len(schemas))
        with self.assertRaises(ValueError):phase_tools(schemas,'unknown')
        self.assertIn('独立报告请求', phase_message(remaining_rounds=1,batch_mode=True)['content'])
        self.assertNotIn('调查即将结束', phase_message(remaining_rounds=3,batch_mode=True)['content'])


if __name__ == '__main__':unittest.main()
