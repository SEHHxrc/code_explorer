"""累计证据预算：唯一行计费、搜索旁路防护、冻结输入与正常报告路径。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.tools.base import ToolContext
from backend.app.experiments.audit.recorder import AuditRecorder
from backend.app.experiments.audit.repository import AuditRepository
from backend.app.experiments.cumulative import CumulativeContextBuilder, EvidenceAllowance, INSTRUCTIONS, prepare_inputs, tool_registry, SELECTION_VERSION, NAVIGATION_PROTOCOL
from backend.app.experiments.cumulative_trial import actual_coverage
from backend.app.experiments.repository import ComparisonRecord, ExperimentRepository
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.settings import get_model_limits
from backend.app.models import Base
from test.test_on_demand_evidence import fixture_pack, LEDGER_JSON


class CumulativeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'CODE_EXPLORER_LLM_PROVIDER':'compatible',
            'CODE_EXPLORER_LLM_MODEL':'fixture','CODE_EXPLORER_LLM_BASE_URL':'https://offline.invalid/v1',
            'CODE_EXPLORER_LLM_MAX_INPUT_TOKENS':'100000','CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS':'2400'}, clear=True)
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.text = 'a = input()\neval(a)\n' + '# filler 不作为答案清单\n'*1000
        (self.root/'app.py').write_bytes(self.text.encode('utf-8'))
        self.inputs = prepare_inputs(self.root, ['app.py'], fixture_pack().model_dump(), 'baseline_fits')
        self.engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.repo, self.store = ExperimentRepository(self.sessions), AgentRunStore(self.sessions)
        self.context = ToolContext(project_id='p',user_id='u',project_root=self.root,artifact={})

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()
        self.env.stop()

    def test_fixed_conditions_and_private_source_not_in_prompt(self):
        for condition in ('baseline_fits','neither_fits'):
            inputs = prepare_inputs(self.root,['app.py'],fixture_pack().model_dump(),condition)
            budgets = []
            for strategy, prepared in inputs.items():
                frozen = prepared['_cumulative_input']
                costs = frozen['source_bytes'] + len(frozen['static_text'].encode())
                self.assertEqual(condition=='baseline_fits' and strategy=='baseline', costs<=frozen['budget_bytes'])
                budgets.append(frozen['budget_bytes'])
                packet = CumulativeContextBuilder().build(project_id='p',question='q',artifact=prepared)
                self.assertNotIn('# filler',packet.prompt_context)
                self.assertNotIn('eval(a)',packet.prompt_context)
            self.assertEqual(*budgets)
        with self.assertRaises(ValueError):prepare_inputs(self.root,['app.py'],fixture_pack().model_dump(),'bad')

    async def test_search_and_reads_share_quota_and_duplicates_are_free(self):
        frozen = self.inputs['baseline']['_cumulative_input'] | {'budget_bytes':len('a = input()\neval(a)\n'.encode())}
        registry = tool_registry(frozen)
        read = await registry.execute('read_file_range',self.context,{'path':'app.py','start_line':1,'end_line':2})
        self.assertEqual(0,read.content['allowance']['remaining_bytes'])
        again = await registry.execute('search_project_text_many',self.context,{'queries':['eval(a)'],'limit':50})
        self.assertEqual(1,len(again.content['matches']))
        self.assertEqual(read.content['allowance']['source_bytes_returned'],again.content['allowance']['source_bytes_returned'])
        denied = await registry.execute('read_file_range',self.context,{'path':'app.py','start_line':3,'end_line':10})
        self.assertEqual('',denied.content['text'])
        self.assertTrue(denied.content['budget_limited'])
        self.assertEqual(3,denied.content['next_start_line'])
        search = await registry.execute('search_project_text_many',self.context,{'queries':['filler'],'limit':50})
        self.assertEqual([],search.content['matches'])
        self.assertFalse(search.content['scope']['results_complete'])
        with self.assertRaises(ValueError):await registry.execute('read_file_range',self.context,{'path':'../outside.py','start_line':1,'end_line':2})

    async def test_whole_lines_unicode_newlines_and_per_call_limit(self):
        frozen = self.inputs['baseline']['_cumulative_input']
        registry = tool_registry(frozen)
        read = await registry.execute('read_file_range',self.context,{'path':'app.py','start_line':1,'end_line':999})
        self.assertEqual(200,read.content['end_line'])
        self.assertEqual(201,read.content['next_start_line'])
        self.assertEqual(len(''.join(self.text.splitlines(keepends=True)[:200]).encode()),read.content['allowance']['source_bytes_returned'])
        zero = EvidenceAllowance(frozen | {'budget_bytes':1})
        self.assertIsNone(zero.take('app.py',1))
        self.assertEqual(0,zero.source_bytes)

    async def test_complete_raw_capacity_static_shortfall_and_run_isolation(self):
        for strategy in ('baseline','security_evidence'):
            frozen = self.inputs[strategy]['_cumulative_input']
            registry = tool_registry(frozen)
            last = None
            for start in range(1, len(self.text.splitlines()) + 1, 200):
                last = await registry.execute('read_file_range',self.context,{'path':'app.py','start_line':start,'end_line':start+199})
            self.assertEqual(strategy=='baseline',last.content['allowance']['all_source_returned'])
            self.assertGreaterEqual(last.content['allowance']['remaining_bytes'],0)
        untouched = EvidenceAllowance(self.inputs['baseline']['_cumulative_input'])
        self.assertEqual(set(),untouched.seen)
        self.assertEqual(0,untouched.source_bytes)

    async def _check_report_contract(self):
        record = ComparisonRecord('pair','p','u','q','raw','static',{'left':'baseline','right':'security_evidence'},['baseline','security_evidence'])
        self.repo.create_pair(record,AgentRunRequest(question='q',max_steps=2,model='fixture'),cumulative_inputs=self.inputs)
        for run_id,strategy in [('raw','baseline'),('static','security_evidence')]:
            artifact = self.repo.full_source_input(run_id,'u')
            registry = tool_registry(artifact['_cumulative_input'])
            manager = AgentRunManager(store=self.store,tools=registry,context_builder=CumulativeContextBuilder(),instructions=INSTRUCTIONS)
            manager.audit = AuditRecorder(AuditRepository(self.sessions),run_id,'u')
            manager.audit_artifact = artifact
            response = {'model':'fixture','choices':[{'finish_reason':'stop','message':{'content':LEDGER_JSON}}],
                        'usage':{'prompt_tokens':100,'completion_tokens':40,'total_tokens':140}}
            provider = OpenAICompatibleProvider(provider_name='compatible',base_url='https://offline.invalid/v1',model='fixture',model_limits=get_model_limits('fixture'))
            with patch('backend.app.agents.orchestrator.create_model_provider',return_value=provider),patch('backend.app.llm.providers.compatible_provider.post_json',new=AsyncMock(return_value=response)):
                await manager._run(run_id=run_id,project_id='p',user_id='u',project_root=str(self.root),artifact=artifact,request=AgentRunRequest(question='q',max_steps=2,model='fixture'))
            self.assertEqual('completed',self.store.get(run_id,'u').status)
        self.assertEqual(tool_registry(self.inputs['baseline']['_cumulative_input']).schemas(),tool_registry(self.inputs['security_evidence']['_cumulative_input']).schemas())

    async def test_source_complete_only_for_raw_and_report_audit_contracts_match(self):
        await self._check_report_contract()

    async def test_navigation_uses_same_worker_report_pipeline_and_audited_contract(self):
        self.inputs=prepare_inputs(self.root,['app.py'],fixture_pack().model_dump(),'baseline_fits')
        self.assertEqual(NAVIGATION_PROTOCOL,self.inputs['baseline']['_experiment_protocol'])
        await self._check_report_contract()
        view=ExperimentComparisonService(repository=self.repo,run_store=self.store,projects=Mock()).get('pair','u')
        self.assertEqual(NAVIGATION_PROTOCOL,view['protocol'])
        for lane in view['lanes'].values():
            self.assertEqual(SELECTION_VERSION,lane['metrics']['execution_policy']['static_selection'])

    def test_static_ceiling_does_not_change_control_prompt_tools_source_or_common_budget(self):
        newer=prepare_inputs(self.root,['app.py'],fixture_pack().model_dump(),'baseline_fits',static_max_bytes=8000)
        smaller=prepare_inputs(self.root,['app.py'],fixture_pack().model_dump(),'baseline_fits',static_max_bytes=4000)
        before=smaller['baseline']['_cumulative_input'];after=newer['baseline']['_cumulative_input']
        self.assertEqual(before['source_manifest'],after['source_manifest'])
        self.assertEqual(before['budget_bytes'],after['budget_bytes'])
        self.assertEqual(tool_registry(before).schemas(),tool_registry(after).schemas())
        old_packet=CumulativeContextBuilder().build(project_id='p',question='q',artifact=smaller['baseline'])
        new_packet=CumulativeContextBuilder().build(project_id='p',question='q',artifact=newer['baseline'])
        self.assertEqual(old_packet.prompt_context,new_packet.prompt_context)
        newer['baseline']['_cumulative_input']['selector_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'选择器实现'):
            CumulativeContextBuilder().build(project_id='p',question='q',artifact=newer['baseline'])

    def test_actual_coverage_excludes_unanswered_transfers_and_unshown_tool_results(self):
        record = ComparisonRecord('pair','p','u','q','raw','static',{'left':'baseline','right':'security_evidence'},['baseline','security_evidence'])
        self.repo.create_pair(record,AgentRunRequest(question='q',max_steps=2),cumulative_inputs=self.inputs)
        audit = AuditRepository(self.sessions)
        payload = {'visible':{'messages':[{'role':'tool','content':json.dumps({'content':{
            'path':'app.py','text':'1: a = input()','start_line':1,'end_line':1}})}]}}
        identifier = audit.append('raw','u','request',payload)
        frozen = self.inputs['baseline']['_cumulative_input']
        self.assertEqual(0,actual_coverage(self.repo,'raw',frozen)['unique_source_lines_shown'])
        audit.append('raw','u','response',{'request_id':identifier})
        coverage = actual_coverage(self.repo,'raw',frozen)
        self.assertEqual(1,coverage['unique_source_lines_shown'])
        self.assertEqual(len('a = input()\n'.encode()),coverage['unique_source_bytes_shown'])
        self.assertFalse(coverage['all_source_shown'])


if __name__=='__main__':unittest.main()
