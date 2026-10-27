"""路径族导航/完成进度与有界知识 Agent 回归；全程使用内存模型与临时数据。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.agents.api_knowledge import ApiKnowledgeAgent
from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.tools.base import ToolContext
from backend.app.api.agent import router
from backend.app.experiments.contracts import ComparisonRequest
from backend.app.experiments.tools import create_experiment_tool_registry
from backend.app.llm.base import ModelProvider, ModelTurn
from backend.app.llm.http import ModelEndpointError
from backend.app.llm.observation import WIRE_OBSERVER, observe_requests
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.settings import ModelLimits
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.services.security_analysis.api_contracts import ApiKnowledgeRequest, ApiModelProposal
from backend.app.services.security_analysis.evidence_query import SecurityEvidenceCatalog
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector
from test import test_evidence_selection as fixtures
from test import test_on_demand_evidence as conversations


def family_pack():
    """同端点三条路线：原值流、较长值流、结构线索，另一个无 Source 的操作。"""
    pack = fixtures.fixture()
    second = copy.deepcopy(pack.dataflows['flow'])
    second.flow_id = 'long-flow'
    second.steps.append(second.steps[0].model_copy(update={'step_id': 'long-step', 'order': 1}))
    pack.dataflows[second.flow_id] = second
    pack.candidates.extend([
        pack.candidates[0].model_copy(update={'candidate_id': 'long', 'dataflow_id': 'long-flow'}),
        pack.candidates[0].model_copy(update={'candidate_id': 'hint', 'dataflow_id': None, 'dataflow_verified': False}),
    ])
    pack.facts['other'] = fixtures.fact('other', 'sink', 33)
    return pack


class PathFamilyTests(unittest.IsolatedAsyncioTestCase):
    async def test_family_browsing_never_emits_a_route_selected_event(self):
        group_id = SecurityEvidenceCatalog(conversations.fixture_pack().model_dump()).search()['items'][0]['id']

        class Model(conversations.BatchModel):
            """模拟浏览、取路线、提交与汇总四次正常请求，不调用外部模型。"""

            async def generate_with_tools(self, *, instructions, messages, tools, **kwargs):
                self.calls.append(messages)
                step = len(self.calls)
                if step <= 2:
                    identifier = group_id if step == 1 else 'c1'
                    return ModelTurn('', self.name, self.model,
                        (conversations.ToolCall(f'read-{step}', 'get_static_security_evidence', {'evidence_id': identifier}),), conversations.TOOLS)
                if step == 3:
                    return ModelTurn('', self.name, self.model, (conversations.ToolCall('submit', 'submit_analysis_batch', {
                        'ledger_json': conversations.LEDGER_JSON, 'evidence_ids': ['c1'], 'scopes': []}),), conversations.TOOLS)
                return ModelTurn(conversations.LEDGER_JSON, self.name, self.model, metadata=conversations.STOP)

        helper = conversations.BatchConversationTests()
        model, store = await helper.run_model(True, provider=Model(True), max_steps=3)
        self.assertEqual('completed', store.status, [(name, event) for name, event in store.events if name.endswith('failed')])
        selected = [event for name, event in store.events if name == 'batch.selected']
        self.assertEqual([['c1']], [event['evidence_ids'] for event in selected])

    def test_initial_family_is_compact_and_legacy_pack_is_not_mutated(self):
        pack = family_pack()
        before = pack.model_dump()
        catalog = SecurityEvidenceCatalog(before)
        summary = json.loads(catalog.summary())
        self.assertEqual('groups', summary['view'])
        self.assertEqual(2, len(summary['items']))
        group = summary['items'][0]
        self.assertEqual(3, group['retained_routes'])
        self.assertEqual({'propagation_path': 2, 'operation_lead': 1}, group['route_kinds'])
        self.assertNotIn('"sequence":', json.dumps(summary))
        self.assertNotIn('value = raw_value', json.dumps(summary))
        self.assertEqual(before, pack.model_dump())
        self.assertEqual(before, catalog.pack.model_dump())

    async def test_browsing_does_not_select_and_completed_route_keeps_siblings(self):
        pack = family_pack()
        with tempfile.TemporaryDirectory() as directory:
            context = ToolContext(project_id='p', user_id='u', project_root=Path(directory), artifact={'security_evidence': pack.model_dump()})
            tools = create_experiment_tool_registry(with_evidence=True)
            group_id = context.evidence_index.security.search()['items'][0]['id']
            navigation = await tools.execute('get_static_security_evidence', context, {'evidence_id': group_id})
            self.assertEqual('group', navigation.content['view'])
            self.assertEqual(set(), context.batches.active_ids)
            self.assertEqual([], navigation.evidence)
            chosen = navigation.content['items'][0]['id']
            await tools.execute('get_static_security_evidence', context, {'evidence_id': chosen})
            self.assertEqual({chosen}, context.batches.active_ids)
            self.assertEqual(set(), context.batches.visible_ids)  # 工具执行不等于模型已看到。
            context.batches.active_ids.clear()
            context.batches.completed_ids.add(chosen)
            navigation = await tools.execute('get_static_security_evidence', context, {'evidence_id': group_id})
            self.assertEqual('partial', navigation.content['group']['progress'])
            self.assertEqual(2, navigation.content['group']['remaining_routes'])
            self.assertNotIn(chosen, [row['id'] for row in navigation.content['items']])
            with self.assertRaises(ValueError):
                await tools.execute('get_static_security_evidence', context, {'evidence_id': chosen})

    def test_group_route_paging_cursor_and_exclusions_are_bound_together(self):
        catalog = SecurityEvidenceCatalog(family_pack().model_dump())
        group_id = catalog.search()['items'][0]['id']
        first = catalog.search(path_group_id=group_id, limit=1)
        second = catalog.search(path_group_id=group_id, cursor=first['next_cursor'], limit=1)
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        with self.assertRaises(ValueError):
            catalog.search(path_group_id=group_id, cursor=first['next_cursor'], excluded={'long'})
        with self.assertRaises(ValueError):
            catalog.search(cursor=first['next_cursor'])
        seen, offset = [], 0
        while True:
            page = catalog.detail(group_id, offset=offset, limit=1)
            seen.extend(row['id'] for row in page['items'])
            if not page['has_more']:
                break
            offset = page['next_offset']
        self.assertEqual({'candidate', 'long', 'hint'}, set(seen))
        excluded = set(seen)
        self.assertEqual('all_retained_reported', catalog.detail(group_id, excluded=excluded)['group']['progress'])
        self.assertEqual(['other'], [row['id'] for row in catalog.search(excluded=excluded)['items']])
        with self.assertRaises(ValueError):
            catalog.detail(group_id, section='flow')

    def test_family_collision_is_rejected_and_budgets_are_common(self):
        pack = family_pack()
        pack.candidates[0].path_group_id = 'collision'
        pack.candidates[1].path_group_id = 'collision'
        pack.candidates[1].sink_fact_id = 'other'
        pack.dataflows['long-flow'].sink_fact_id = 'other'
        with self.assertRaises(ValueError):
            SecurityEvidenceCatalog(pack.model_dump())
        self.assertEqual(12, ComparisonRequest(question='test').max_steps)
        self.assertEqual(12, AgentRunRequest(question='test').max_steps)
        self.assertEqual(36, ComparisonRequest(question='test', max_steps=36).max_steps)
        with self.assertRaises(ValueError):
            ComparisonRequest(question='test', max_steps=37)

    def test_scoped_family_navigation_does_not_leak_outside_call_locations(self):
        pack = family_pack()
        pack.dataflows['flow'].steps[0].kind = 'call'
        pack.dataflows['flow'].steps[0].location.path = 'outside/private.py'
        selector = SecurityEvidenceSelector(pack, {'src/impl.py'})
        row = next(item for item in selector.rows() if item['id'] == 'candidate')
        navigation = selector.route(row)
        self.assertEqual('outside_scope', navigation['locators_withheld_reason'])
        self.assertNotIn('outside/private.py', json.dumps(navigation))
        self.assertEqual([], navigation['via'])


class KnowledgeAgentTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        """仅提交 API 身份，不提交项目源码或用户实参。"""
        return ApiKnowledgeRequest(language='python', package='vendor', qualified_name='vendor.filter',
                                   argument_count=1, return_mode='single', return_count=1)

    def provider(self, *texts):
        """模型调用仅返回内存建议，供应商用量保留真实零/未知的区别。"""
        provider = Mock(spec=ModelProvider)
        provider.generate_with_tools = AsyncMock(side_effect=[ModelTurn(text, 'mock', 'mock', metadata=ModelResponseMetadata(
            finish_reason='stop', usage=ModelUsage(10, 20, 30))) for text in texts])
        return provider

    async def test_format_retry_is_separate_and_parent_wire_observer_is_restored(self):
        provider = self.provider('{', '{"returns":[{"component":0,"depends_on_arguments":[0]}]}')
        parent = Mock()
        with observe_requests(parent):
            result = await ApiKnowledgeAgent(provider).run(self.request())
            self.assertIs(parent, WIRE_OBSERVER.get())
        self.assertEqual('proposed', result['status'])
        self.assertEqual(2, result['model_requests'])
        self.assertEqual(60, result['usage']['total_tokens'])
        self.assertIn('schema_feedback', provider.generate_with_tools.call_args.kwargs['prompt'])
        parent.sent.assert_not_called()
        self.assertNotIn('max_steps', json.dumps(result))
        json.dumps(result)

    async def test_typed_return_condition_and_unknown_are_not_safety_proof(self):
        proposal = {'returns': [
            {'component': 0, 'member_path': [0], 'depends_on_arguments': [0], 'sanitized_for': ['html_text'],
             'required_conditions': [{'slot': {'kind': 'return', 'index': 0, 'member_path': [1]}, 'operator': 'eq', 'value': True}]},
            {'component': 0, 'member_path': [1], 'role': 'status', 'depends_on_arguments': [0]},
            {'component': 0, 'member_path': [2], 'depends_on_arguments': None},
        ]}
        result = await ApiKnowledgeAgent(self.provider(json.dumps(proposal))).run(self.request())
        self.assertEqual('llm_proposal', result['provenance'])
        self.assertEqual('schema_and_slot_bounds_only', result['validation_scope'])
        self.assertIsNone(result['proposal']['returns'][2]['depends_on_arguments'])
        proposal['returns'][0]['required_conditions'][0]['slot']['index'] = 2
        with self.assertRaises(ValueError):
            ApiModelProposal.model_validate(proposal).check_request(self.request())
        proposal['returns'][0]['required_conditions'][0] = {
            'slot': {'kind': 'return', 'index': 0}, 'operator': 'is_null', 'value': 'contradiction'}
        with self.assertRaises(ValueError):
            ApiModelProposal.model_validate(proposal)

    async def test_no_extra_replay_for_network_error_or_empty_response(self):
        provider = self.provider('')
        result = await ApiKnowledgeAgent(provider, max_model_requests=4).run(self.request())
        self.assertEqual(1, provider.generate_with_tools.await_count)
        self.assertEqual('unresolved', result['status'])
        provider.generate_with_tools = AsyncMock(side_effect=ModelEndpointError(status_code=429))
        result = await ApiKnowledgeAgent(provider, max_model_requests=4).run(self.request())
        self.assertEqual('failed', result['status'])
        self.assertIsNone(result['usage']['total_tokens'])
        self.assertEqual(1, provider.generate_with_tools.await_count)

    async def test_explicit_http_entry_uses_own_budget_and_default_model_provider(self):
        app = FastAPI()
        app.include_router(router)
        provider = self.provider('{"returns":[]}')
        with patch('backend.app.api.agent.create_model_provider', return_value=provider):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/agent/api-knowledge', json={'api': self.request().model_dump(), 'max_model_requests': 1})
                self.assertEqual(200, response.status_code)
                self.assertEqual(1, response.json()['data']['model_requests'])
                invalid = await client.post('/api/agent/api-knowledge', json={'api': self.request().model_dump(), 'max_model_requests': 5})
                self.assertEqual(422, invalid.status_code)

    async def test_429_uses_shared_transport_retry_without_spending_an_extra_agent_turn(self):
        provider = OpenAICompatibleProvider(provider_name='fixture', base_url='http://mock', model='fixture',
            max_output_tokens=512, model_limits=ModelLimits(0, 512, max_input_tokens=100000))
        parent = Mock()

        def send(method, url, payload, headers, timeout, *, attempt=1, **kwargs):
            observer = WIRE_OBSERVER.get()
            identifier = observer.sent(payload, attempt, wire_sha256='fixture', wire_bytes=100)
            if attempt == 1:
                observer.failed(identifier, 'ModelEndpointError', diagnostics={'status_code': 429})
                raise ModelEndpointError(status_code=429, error_code='slow_down', retry_after='0')
            response = {'choices': [{'message': {'content': '{"returns":[{"component":0}]}'}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
            observer.received(identifier, response)
            return response

        with patch('backend.app.llm.http._request_json_sync', side_effect=send) as wire, \
             patch('backend.app.llm.http.asyncio.sleep', new=AsyncMock()), \
             patch('backend.app.llm.http.random.uniform', return_value=0), observe_requests(parent):
            result = await ApiKnowledgeAgent(provider, max_model_requests=1).run(self.request())
            self.assertIs(parent, WIRE_OBSERVER.get())
        self.assertEqual(2, wire.call_count)
        self.assertEqual(1, result['model_requests'])
        self.assertEqual(2, len(result['transport_attempts']))
        self.assertEqual(429, result['retry_waits'][0]['status_code'])
        self.assertIsNone(result['usage']['total_tokens'])  # 失败尝试是否计费未知，不能补零。
        self.assertEqual(30, result['transport_attempts'][1]['metadata']['usage']['total_tokens'])
        parent.sent.assert_not_called()


if __name__ == '__main__':
    unittest.main()
