"""历史实验的分页、盲态、所有权与原快照回归；只使用内存数据库，不调用模型。"""

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.experiment import router
from backend.app.core.deps import get_current_user
from backend.app.models import Base, AgentRunModel, AgentEventModel, ExperimentComparisonModel, ExperimentReviewModel, ExperimentAuditRecordModel, ExperimentAuditSessionModel
from backend.app.experiments.repository import ExperimentRepository
from backend.app.experiments.service import ExperimentComparisonService
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.experiments.contracts import ExperimentError
from backend.app.experiments.citations import check_citations
from backend.app.experiments.context import SECURITY_EXPERIMENT_INSTRUCTIONS
from backend.app.experiments.baseline.strategy import BASELINE_INSTRUCTIONS
from backend.app.experiments.report import REPORT_INSTRUCTIONS, REPORT_VERSION, REPORT_END, assess_answer
from backend.app.llm.response_metadata import ModelResponseMetadata


class ExperimentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.repository = ExperimentRepository(self.sessions)
        self.projects = Mock()
        self.projects.get_owned.side_effect = lambda project_id, user_id: object() if (project_id, user_id) == ('project', 'alice') else None
        self.service = ExperimentComparisonService(repository=self.repository, projects=self.projects)
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            for index in range(3):
                identifiers = [f'b{index}', f's{index}']
                session.add(ExperimentComparisonModel(id=f'pair{index}', project_id='project', user_id='alice', question=f'question{index}',
                    baseline_run_id=identifiers[0], graph_run_id=identifiers[1], blind_order={'left': 'security_evidence', 'right': 'baseline'},
                    execution_order=['security_evidence', 'baseline'], created_at=now + timedelta(seconds=index)))
                for identifier in identifiers:
                    session.add(AgentRunModel(id=identifier, project_id='project', user_id='alice', question='q',
                        status=['completed', 'failed', 'cancelled'][index], model='old-model', answer='原始回答', max_steps=4))
            session.add(ExperimentReviewModel(comparison_id='pair0', user_id='alice', preferred_lane='tie', scores={'left': {}, 'right': {}}, notes='原始评分'))
            session.add(ExperimentComparisonModel(id='other', project_id='project', user_id='bob', question='PRIVATE_OTHER_USER',
                baseline_run_id='bob-b', graph_run_id='bob-s', blind_order={'left':'baseline','right':'security_evidence'}, execution_order=['baseline','security_evidence']))
            for identifier in ('b0', 's0'):
                session.add(ExperimentAuditSessionModel(run_id=identifier, project_id='project', user_id='alice', comparison_id='pair0',
                    status='complete', reserved_bytes=0, payload_bytes=0, expires_at=now + timedelta(days=1)))
                session.add(ExperimentAuditRecordModel(run_id=identifier, kind='manifest', payload={'instructions':'旧版共同提示词',
                    'instructions_sha256':'original-prompt-hash', 'report_version':'concise-security-v2', 'tools_sha256':'original-tools',
                    'files_sha256':'original-files', 'static_artifact_sha256':'original-artifact',
                    'files': {'PRIVATE_SOURCE': {'lines':1}}, 'execution_policy': {'max_tool_rounds':4}}))
                session.add(ExperimentAuditRecordModel(run_id=identifier, kind='request', payload={'visible':{'messages':'PRIVATE_REQUEST'}}))
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_format_resubmission_counts_both_responses_and_keeps_rejection_visible(self):
        """首次无效 JSON 的用量不能被后续成功覆盖，亦不能重复累加。"""
        events = [
            ('run.started', {}), ('model.started', {}),
            ('model.completed', {'metadata': {'finish_reason': 'stop',
                'usage': {'input_tokens': 100, 'output_tokens': 40, 'total_tokens': 140}}}),
            ('report.format_rejected', {'step': 1, 'category': 'syntax'}),
            ('report.format_retry_started', {'step': 1, 'attempt': 1}), ('model.started', {}),
            ('model.completed', {'metadata': {'finish_reason': 'stop',
                'usage': {'input_tokens': 200, 'output_tokens': 60, 'total_tokens': 260}}}),
            ('run.completed', {'termination_reason': 'model_final_answer'}),
        ]
        with self.sessions() as session:
            session.add_all(AgentEventModel(run_id='b0', sequence=index, event_type=kind, payload=payload)
                            for index, (kind, payload) in enumerate(events, 1))
            session.commit()
        metrics = collect_run_metrics('b0', 'alice', session_factory=self.sessions)
        self.assertEqual((2, 300, 100, 400), (metrics['model_requests'], metrics['actual_input_tokens'],
            metrics['actual_output_tokens'], metrics['actual_total_tokens']))
        self.assertEqual({'retries': 1, 'rejections': [{'step': 1, 'category': 'syntax'}], 'skipped': []}, metrics['report_format'])

    def test_pagination_keeps_failures_and_blind_lanes_without_loading_bodies(self):
        page = self.service.history('project','alice',limit=2)
        self.assertEqual(3,page['total'])
        self.assertEqual(['pair2','pair1'],[item['id'] for item in page['items']])
        self.assertTrue(page['has_more'])
        self.assertEqual('cancelled',page['items'][0]['lanes']['left']['status'])
        self.assertEqual('failed',page['items'][1]['lanes']['right']['status'])
        self.assertEqual('pair0',self.service.history('project','alice',offset=2,limit=2)['items'][0]['id'])
        self.assertEqual('tie',self.service.history('project','alice',offset=2,limit=2)['items'][0]['preferred_lane'])
        self.assertIsNone(page['items'][0]['preferred_lane'])
        text=json.dumps(page,default=str)
        for forbidden in ('原始回答','PRIVATE','baseline','security_evidence','blind_order'):
            self.assertNotIn(forbidden,text)

    def test_project_and_comparison_ownership_and_page_bounds(self):
        for project_id,user_id,offset,limit in [('project','bob',0,20),('missing','alice',0,20),('project','alice',-1,20),('project','alice',0,51)]:
            with self.assertRaises(ExperimentError):
                self.service.history(project_id,user_id,offset=offset,limit=limit)
        with self.assertRaises(ExperimentError):
            self.service.history_detail('pair0','bob')
        with self.assertRaises(ExperimentError):
            self.service.history_detail('other','alice')

    def test_detail_reuses_original_prompt_and_review_without_full_audit(self):
        with self.sessions() as session:
            before = [(row.id,row.payload) for row in session.query(ExperimentAuditRecordModel).all()]
        view=self.service.history_detail('pair0','alice')
        self.assertEqual('原始评分',view['review']['notes'])
        self.assertEqual('security_evidence',view['reveal']['left'])
        self.assertEqual('原始回答',view['lanes']['left']['run']['answer'])
        for lane in view['lanes'].values():
            snapshot=lane['prompt_snapshot']
            self.assertEqual('旧版共同提示词',snapshot['instructions'])
            self.assertEqual('concise-security-v2',snapshot['report_version'])
            self.assertEqual('original-prompt-hash',snapshot['instructions_sha256'])
        self.assertNotIn('PRIVATE',json.dumps(view,ensure_ascii=False,default=str))
        with self.sessions() as session:
            self.assertEqual(before,[(row.id,row.payload) for row in session.query(ExperimentAuditRecordModel).all()])

    def test_new_review_dimensions_round_trip_without_migrating_old_reviews(self):
        from backend.app.experiments.contracts import BlindReviewRequest, LaneScores
        original = self.repository.get_review('pair0', 'alice')
        scores = LaneScores(discovery_breadth=5, chain_verification=1, conclusion_credibility=2)
        self.repository.save_review('pair1', 'alice', BlindReviewRequest(preferred_lane='tie', left=scores, right=scores))
        saved = self.repository.get_review('pair1', 'alice')
        self.assertEqual('independent-review-v2', saved['scores']['rubric_version'])
        self.assertEqual({'discovery_breadth':5, 'chain_verification':1, 'conclusion_credibility':2}, saved['scores']['left'])
        self.assertEqual(original, self.repository.get_review('pair0', 'alice'))

    def test_missing_audit_is_unknown_and_unreviewed_trial_remains_blind(self):
        view=self.service.history_detail('pair1','alice')
        self.assertIsNone(view['reveal'])
        self.assertIsNone(view['review'])
        self.assertEqual({'status':'unavailable'},view['lanes']['left']['prompt_snapshot'])
        self.assertNotIn(SECURITY_EXPERIMENT_INSTRUCTIONS,json.dumps(view,default=str))

    def test_lightweight_prompt_survives_body_expiry_and_does_not_expose_extra_fields(self):
        with self.sessions() as session:
            session.add(AgentEventModel(run_id='s1', sequence=1, event_type='experiment.snapshot', payload={
                'instructions':'运行时冻结的共同提示词', 'instructions_sha256':'frozen', 'report_version':'concise-security-v3',
                'unexpected_source':'PRIVATE_SOURCE', 'request':'PRIVATE_REQUEST'}))
            session.commit()
        view=self.service.history_detail('pair1','alice')
        self.assertEqual('frozen',view['lanes']['left']['prompt_snapshot']['instructions_sha256'])
        self.assertNotIn('PRIVATE',json.dumps(view,ensure_ascii=False,default=str))

    def test_http_history_and_validation(self):
        app=FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user]=lambda: {'user_id':'alice'}
        async def scenario():
            with patch('backend.app.api.experiment.comparison_service',self.service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                    response=await client.get('/api/experiments/projects/project/comparisons?limit=2')
                    self.assertEqual(200,response.status_code)
                    self.assertEqual(3,response.json()['data']['total'])
                    self.assertEqual(422,(await client.get('/api/experiments/projects/project/comparisons?limit=51')).status_code)
                    response=await client.get('/api/experiments/comparisons/pair0/history')
                    self.assertEqual('旧版共同提示词',response.json()['data']['lanes']['left']['prompt_snapshot']['instructions'])
                    self.assertEqual(404,(await client.get('/api/experiments/comparisons/other/history')).status_code)
        asyncio.run(scenario())


class CitationAndPromptRulesTests(unittest.TestCase):
    def test_independent_scores_are_versioned_and_never_computed_from_counts(self):
        from backend.app.experiments.contracts import BlindReviewRequest, LaneScores
        scores = LaneScores(discovery_breadth=5, chain_verification=1, conclusion_credibility=1)
        review = BlindReviewRequest(preferred_lane='tie', left=scores, right=scores)
        self.assertEqual('independent-review-v2', review.rubric_version)
        self.assertEqual({'discovery_breadth':5, 'chain_verification':1, 'conclusion_credibility':1}, review.left.model_dump())
        for invalid in ({'discovery_breadth':5, 'chain_verification':0, 'conclusion_credibility':1},
                        {'discovery_breadth':5, 'chain_verification':True, 'conclusion_credibility':1},
                        {'correctness':5, 'completeness':5, 'evidence':5, 'hallucination_control':5}):
            with self.assertRaises(ValueError): LaneScores(**invalid)

    def test_manual_cases_cover_distinct_projects_without_leaking_answers_into_prompt(self):
        cases=json.loads(Path(__file__).with_name('security_report_cases.json').read_text(encoding='utf-8'))['cases']
        self.assertEqual({'wrong_value_sanitized','guard_before_process','factory_hidden_operation','separate_roots_shared_endpoint'}, {case['id'] for case in cases})
        for case in cases:
            self.assertTrue(case['files'])
            self.assertGreaterEqual(len(case['checks']),3)
            self.assertNotIn(case['id'],SECURITY_EXPERIMENT_INSTRUCTIONS)

    def test_abbreviation_resolves_only_unique_snapshot_path_and_still_checks_receipts(self):
        report='[main.py:2] [main.py:2] [other.py:5]'
        manifest={'src/main.py':{'lines':4},'src/other.py':{'lines':4}}
        seen={'src/main.py':{2}}
        result=check_citations(report,manifest,seen,seen)
        self.assertEqual(2,result['checked'])
        warnings=result['warnings']
        self.assertEqual('src/main.py',warnings[0]['resolved_path'])
        self.assertEqual(['path_abbreviation_resolved','path_abbreviation_resolved','line_out_of_range'],[item['reason'] for item in warnings])
        self.assertEqual('[main.py:2] [main.py:2] [other.py:5]',report)

    def test_ambiguity_syntax_real_errors_and_not_shown_are_separate(self):
        report='[common.py:1] [同文件:1] [a.py:1; b.py:2] [a.py:1,2] [missing.py:1] [src/safe.py:2]'
        manifest={'one/common.py':{'lines':2},'two/common.py':{'lines':2},'src/safe.py':{'lines':2}}
        result=check_citations(report,manifest,{}, {})
        self.assertEqual(3,result['malformed'])
        self.assertEqual({'path_ambiguous','citation_syntax_invalid','path_not_in_snapshot','source_not_shown'}, {item['reason'] for item in result['warnings']})
        self.assertEqual('not_performed',result['semantic_validation'])

    def test_both_groups_share_generic_priority_guard_and_root_cause_rules(self):
        self.assertEqual(BASELINE_INSTRUCTIONS,SECURITY_EXPERIMENT_INSTRUCTIONS)
        for rule in ('真实危险操作实现','未闭合链路','对应防护'):
            self.assertIn(rule,SECURITY_EXPERIMENT_INSTRUCTIONS)
        for rule in ('不同根因即使位于同一接口','进入危险操作的值','危险上下文','危险操作之前','完整项目相对路径'):
            self.assertIn(rule,REPORT_INSTRUCTIONS)
        for sample in ('audio2sheet','Spleeter','safe_id','AES'):
            self.assertNotIn(sample,SECURITY_EXPERIMENT_INSTRUCTIONS)
        self.assertEqual('ledger-security-v8',REPORT_VERSION)

    def test_structural_pass_does_not_validate_wrong_variable_guard_or_missing_implementation(self):
        report='## 结论\n测试。\n## 已核实问题\n附近的清洗使所有值安全。\n## 未确认风险\n未读取被调用实现但认为无风险。\n## 范围与局限\n只读测试。\n'+REPORT_END
        result=assess_answer(report,ModelResponseMetadata(finish_reason='stop'),require_report=True)
        self.assertEqual('complete',result['status'])
        self.assertEqual('not_proven',result['semantic_coverage'])


if __name__=='__main__':
    unittest.main()
