"""完整源码条件的差异隔离、零工具单次报告、预算拒绝和冻结审计回归。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import Base, ExperimentAuditRecordModel
from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.tools.base import ToolRegistry
from backend.app.experiments.audit.recorder import AuditRecorder
from backend.app.experiments.audit.repository import AuditRepository
from backend.app.experiments.full_source import FullSourceContextBuilder, prepare_full_source_inputs, FULL_SOURCE_INSTRUCTIONS
from backend.app.experiments.repository import ExperimentRepository, ComparisonRecord
from backend.app.experiments.citations import visible_lines
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.settings import get_model_limits
from test.test_on_demand_evidence import fixture_pack, LEDGER_JSON
from backend.app.experiments.cumulative import prepare_inputs
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector, SELECTION_VERSION


class FullSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'CODE_EXPLORER_LLM_PROVIDER':'compatible', 'CODE_EXPLORER_LLM_MODEL':'fixture',
            'CODE_EXPLORER_LLM_BASE_URL':'https://offline.invalid/v1',
            'CODE_EXPLORER_LLM_MAX_INPUT_TOKENS':'100000', 'CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS':'2400',
        }, clear=True)
        self.environment.start()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root/'app.py').write_text('a = input()\neval(a)\nb = input()\neval(b)\n',encoding='utf-8')
        self.inputs = prepare_full_source_inputs(self.root, ['app.py'], fixture_pack().model_dump())
        self.engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.repository = ExperimentRepository(self.sessions)
        self.store = AgentRunStore(self.sessions)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()
        self.environment.stop()

    def pair(self):
        record = ComparisonRecord('pair','project','user','安全分析','base','static',
            {'left':'baseline','right':'security_evidence'},['baseline','security_evidence'])
        self.repository.create_pair(record,AgentRunRequest(question='安全分析',max_steps=1,model='fixture'),full_source_inputs=self.inputs)

    async def test_trial_rejects_empty_or_unknown_selection_before_import_or_model(self):
        from backend.app.experiments.full_source_trial import run_trials
        for selected in ((), ('unknown-case',)):
            with self.subTest(selected=selected), patch('backend.app.experiments.full_source_trial.ProjectImportService') as importer:
                with self.assertRaises(ValueError):
                    await run_trials(repeats=1, run_model=True, case_ids=selected)
                importer.assert_not_called()

    async def test_trial_reuses_exact_fixture_without_analysis_or_import(self):
        from backend.app.experiments import full_source_trial as trial
        for file in (trial.FIXTURES / 'case_03').iterdir():
            (self.root / file.name).write_bytes(file.read_bytes())
        project = SimpleNamespace(project_id='existing', local_path=str(self.root))
        with patch.object(trial.ProjectRepository, 'get_owned', return_value=project), \
                patch.object(trial.ProjectArtifactRepository, 'load', return_value={'security_evidence':fixture_pack().model_dump()}), \
                patch.object(trial.ProjectAnalysisPipeline, 'analyze') as analyze, \
                patch.object(trial.ProjectImportService, 'import_project') as importer:
            result = await trial.run_trials(repeats=3, run_model=False, case_ids=('case_03',),
                reuse_project_id='existing', stop_after_valid_pair=True, cooldown_seconds=60)
        self.assertEqual('existing', result['projects'][0]['project_id'])
        self.assertEqual([], result['comparisons'])
        analyze.assert_not_called()
        importer.assert_not_called()

    async def test_trial_rejects_drift_or_invalid_stop_options_before_model(self):
        from backend.app.experiments import full_source_trial as trial
        for args in ({'case_ids':('case_01','case_03'),'stop_after_valid_pair':True},
                     {'case_ids':('case_03',),'cooldown_seconds':-1}):
            with self.assertRaises(ValueError):
                await trial.run_trials(repeats=1, run_model=False, **args)
        with patch.object(trial.ProjectRepository, 'get_owned', return_value=SimpleNamespace(project_id='existing',local_path=str(self.root))), \
                patch.object(trial.ProjectArtifactRepository, 'load', return_value={'security_evidence':fixture_pack().model_dump()}):
            with self.assertRaisesRegex(ValueError, '不一致'):
                await trial.run_trials(repeats=1, run_model=False, case_ids=('case_03',),reuse_project_id='existing')

    async def test_trial_keeps_failed_pair_then_stops_after_complete_valid_pair(self):
        from backend.app.experiments import full_source_trial as trial
        for file in (trial.FIXTURES / 'case_03').iterdir():
            (self.root / file.name).write_bytes(file.read_bytes())
        records, claims, states = [], [], {}
        repository, projects, artifacts, store, worker, service = (Mock() for _ in range(6))
        projects.get_owned.return_value = SimpleNamespace(project_id='existing',local_path=str(self.root))
        artifacts.load.return_value = {'security_evidence':fixture_pack().model_dump()}
        repository.create_pair.side_effect = lambda record,*args,**kwargs: records.append(record)
        async def execute(claim):
            states[claim.run_id] = 'failed' if len(claims)<2 and claim.strategy.endswith('security_evidence') else 'completed'
            claims.append(claim)
        worker.execute_claim = AsyncMock(side_effect=execute)
        store.get.side_effect = lambda run_id,user: SimpleNamespace(status=states[run_id],
            model_dump=lambda **kwargs: {'id':run_id,'status':states[run_id]})
        service.get.side_effect = lambda identifier,user: {'id':identifier,'valid_for_review':len(records)==2}
        output = self.root / 'results'
        with patch.object(trial,'init_db'), patch.object(trial,'ExperimentRepository',return_value=repository), \
                patch.object(trial,'ProjectRepository',return_value=projects), patch.object(trial,'ProjectArtifactRepository',return_value=artifacts), \
                patch.object(trial,'AgentRunStore',return_value=store), patch.object(trial,'AgentQueueWorker',return_value=worker), \
                patch.object(trial,'ExperimentComparisonService',return_value=service), patch.object(trial,'_heartbeat',new=AsyncMock()), \
                patch.object(trial,'Path',side_effect=lambda value: output if value=='backend/storage/experiments' else Path(value)):
            result = await trial.run_trials(repeats=3,run_model=True,case_ids=('case_03',),
                                            reuse_project_id='existing',stop_after_valid_pair=True)
        self.assertEqual(4,len(claims))
        self.assertEqual(2,len(result['comparisons']))
        self.assertFalse(result['comparisons'][0]['comparison']['valid_for_review'])
        self.assertIn('failed',{row['status'] for row in result['comparisons'][0]['runs'].values()})
        self.assertTrue(result['comparisons'][1]['comparison']['valid_for_review'])
        self.assertEqual(1,result['valid_pairs'])
        self.assertEqual('first_valid_pair_obtained_not_an_unbiased_quality_sample',result['termination'])

    def test_only_static_suffix_differs_and_complete_lines_are_visible(self):
        base = self.inputs['baseline']['_full_source_input']
        augmented = self.inputs['security_evidence']['_full_source_input']
        self.assertEqual(base['prompt'],augmented['prompt'].split('\nSTATIC_SECURITY_EVIDENCE\n',1)[0])
        self.assertEqual(base['source_sha256'],augmented['source_sha256'])
        source = json.loads(base['prompt'].split('\n',1)[1])
        self.assertEqual((self.root/'app.py').read_bytes().decode('utf-8'),source['files'][0]['text'])
        shown=visible_lines({'messages':[{'role':'user','content':'USER_QUESTION\nq\n\nTRUSTED_STATIC_CONTEXT\n'+base['prompt']}]})
        self.assertEqual({'app.py':{1,2,3,4}},shown)
        self.assertNotIn('submit_analysis_batch',FULL_SOURCE_INSTRUCTIONS)

    def test_frozen_inputs_are_private_owned_audit_records_and_not_history_events(self):
        self.pair()
        self.assertEqual(self.inputs['baseline'],self.repository.full_source_input('base','user'))
        with self.assertRaises(ValueError):self.repository.full_source_input('base','other')
        self.assertEqual([],self.store.events_after('base',0))
        with self.sessions() as session:
            self.assertEqual(2,session.query(ExperimentAuditRecordModel).count())

    def test_configuration_drift_and_insufficient_input_budget_are_rejected_without_truncation(self):
        with patch.dict(os.environ,{'CODE_EXPLORER_LLM_MAX_INPUT_TOKENS':'1000'}):
            with self.assertRaises(ValueError):FullSourceContextBuilder().build(project_id='p',question='q',artifact=self.inputs['baseline'])
            limited=prepare_full_source_inputs(self.root,['app.py'],fixture_pack().model_dump())
            with self.assertRaises(ValueError):FullSourceContextBuilder().build(project_id='p',question='q'*5000,artifact=limited['baseline'])
        with self.assertRaises(ValueError):prepare_full_source_inputs(self.root,['app.py','app.py'],fixture_pack().model_dump())

    def test_oversized_scope_is_rejected_by_bounded_read(self):
        (self.root/'large.py').write_bytes(b'x'*(128*1024+1))
        with self.assertRaises(ValueError):prepare_full_source_inputs(self.root,['large.py'],fixture_pack().model_dump())

    def test_full_and_cumulative_conditions_use_exactly_the_same_static_selection(self):
        (self.root/'app.py').write_text('a = input()\neval(a)\n' + '# filler\n'*4000, encoding='utf-8')
        raw = fixture_pack().model_dump()
        full = prepare_full_source_inputs(self.root, ['app.py'], raw, static_max_bytes=7676)
        cumulative = prepare_inputs(self.root, ['app.py'], raw, 'neither_fits', static_max_bytes=7676)
        text = full['security_evidence']['_full_source_input']['prompt'].split('\nSTATIC_SECURITY_EVIDENCE\n', 1)[1]
        self.assertEqual(text, cumulative['security_evidence']['_cumulative_input']['static_text'])
        self.assertEqual(SecurityEvidenceSelector(raw, {'app.py'}).build(max_bytes=7676, details_available=False), json.loads(text))
        self.assertEqual(SELECTION_VERSION, json.loads(text)['schema_version'])
        self.assertFalse(json.loads(text)['details_available'])

    def test_selector_drift_is_rejected_without_a_model_request(self):
        self.inputs['baseline']['_full_source_input']['selector_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, '选择器实现'):
            FullSourceContextBuilder().build(project_id='p',question='q',artifact=self.inputs['baseline'])

    async def test_both_lanes_make_one_no_tool_request_and_keep_usage_and_citations(self):
        self.pair()
        for run_id, strategy in [('base','baseline'),('static','security_evidence')]:
            provider=OpenAICompatibleProvider(provider_name='compatible',base_url='https://offline.invalid/v1',model='fixture',model_limits=get_model_limits('fixture'))
            manager=AgentRunManager(store=self.store,tools=ToolRegistry([]),context_builder=FullSourceContextBuilder(),instructions=FULL_SOURCE_INSTRUCTIONS)
            manager.audit=AuditRecorder(AuditRepository(self.sessions),run_id,'user')
            manager.audit_artifact=self.inputs[strategy]
            response={'id':run_id,'model':'fixture','choices':[{'finish_reason':'stop','message':{'content':LEDGER_JSON}}],
                      'usage':{'prompt_tokens':100,'completion_tokens':40,'total_tokens':140}}
            with patch('backend.app.agents.orchestrator.create_model_provider',return_value=provider),patch('backend.app.llm.providers.compatible_provider.post_json',new=AsyncMock(return_value=response)) as send:
                await manager._run(run_id=run_id,project_id='project',user_id='user',project_root=str(self.root),artifact=self.inputs[strategy],request=AgentRunRequest(question='安全分析',max_steps=1,model='fixture'))
            self.assertEqual('completed',self.store.get(run_id,'user').status)
            self.assertEqual(1,send.await_count)
            self.assertNotIn('tools',send.call_args.args[1])
            self.assertNotIn('tool_choice',send.call_args.args[1])
            events=self.store.events_after(run_id,0)
            self.assertEqual(['final_report'],[item.payload['phase'] for item in events if item.type=='model.started'])
            self.assertFalse(any(item.type.startswith('batch.') for item in events))

    async def test_changed_source_fails_before_model_request(self):
        self.pair()
        (self.root/'app.py').write_text('changed\n',encoding='utf-8')
        manager=AgentRunManager(store=self.store,tools=ToolRegistry([]),context_builder=FullSourceContextBuilder(),instructions=FULL_SOURCE_INSTRUCTIONS)
        manager.audit=AuditRecorder(AuditRepository(self.sessions),'base','user')
        with patch('backend.app.llm.providers.compatible_provider.post_json',new=AsyncMock()) as send:
            await manager._run(run_id='base',project_id='project',user_id='user',project_root=str(self.root),artifact=self.inputs['baseline'],request=AgentRunRequest(question='安全分析',max_steps=1))
        self.assertEqual('failed',self.store.get('base','user').status)
        self.assertEqual(0,send.await_count)

    async def test_empty_or_unrequested_tool_output_is_not_repaired_or_executed(self):
        self.pair()
        for run_id,strategy,message,finish in [
            ('base','baseline',{'content':None},'stop'),
            ('static','security_evidence',{'content':None,'tool_calls':[{'id':'bad','type':'function',
                'function':{'name':'read_file_range','arguments':'{"path":"app.py"}'}}]},'tool_calls'),
        ]:
            manager=AgentRunManager(store=self.store,tools=ToolRegistry([]),context_builder=FullSourceContextBuilder(),instructions=FULL_SOURCE_INSTRUCTIONS)
            manager.audit=AuditRecorder(AuditRepository(self.sessions),run_id,'user')
            response={'model':'fixture','choices':[{'finish_reason':finish,'message':message}]}
            with patch('backend.app.llm.providers.compatible_provider.post_json',new=AsyncMock(return_value=response)) as send:
                await manager._run(run_id=run_id,project_id='project',user_id='user',project_root=str(self.root),artifact=self.inputs[strategy],request=AgentRunRequest(question='安全分析',max_steps=1))
            self.assertEqual('failed',self.store.get(run_id,'user').status)
            self.assertEqual(1,send.await_count)
            self.assertFalse(any(item.type=='tool.requested' for item in self.store.events_after(run_id,0)))
