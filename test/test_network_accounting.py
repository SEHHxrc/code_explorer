"""仅在临时数据库模拟端点拒绝，不发真实请求、不修改旧记录。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.experiments.audit.recorder import AuditRecorder
from backend.app.experiments.metrics import collect_run_metrics
from backend.app.llm.settings import endpoint_accounting_fingerprint, pre_model_rejection_policy
from backend.app.models import AgentEventModel
from test import test_experiment_audit as fixtures


class NetworkAccountingTests(unittest.TestCase):
    def setUp(self):
        fixtures.AuditTests.setUp(self)
        self.env=patch.dict(os.environ,{'CODE_EXPLORER_LLM_PROVIDER':'compatible','CODE_EXPLORER_LLM_MODEL':'fixture',
            'CODE_EXPLORER_LLM_BASE_URL':'https://offline.invalid/v1'},clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop();fixtures.AuditTests.tearDown(self)

    def simulate(self,status=429,declared=True,error_type='HTTPError'):
        if declared:os.environ['CODE_EXPLORER_LLM_PRE_MODEL_REJECTION_ENDPOINT_SHA256']=endpoint_accounting_fingerprint()
        recorder=AuditRecorder(self.repository,'run','alice')
        policy={'network_accounting':pre_model_rejection_policy()}
        with tempfile.TemporaryDirectory() as directory:recorder.start(Path(directory),{},'instructions',[],'version',policy)
        recorder.planned({'messages':[]},1)
        first=recorder.sent({'messages':[]},1)
        recorder.failed(first,error_type,diagnostics={'attempt':1,'status_code':status,'elapsed_ms':7000})
        recorder.retry_wait(1,30,30.125,status_code=status,completed=True)
        second=recorder.sent({'messages':[]},2)
        recorder.received(second,{'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}})
        summary=recorder.finish()
        now=datetime.now()
        with self.sessions() as session:
            for sequence,kind,payload in ((1,'run.started',{'execution_policy':policy}), (2,'model.started',{}),
                (3,'model.completed',{'metadata':{'usage':{'input_tokens':2,'output_tokens':1,'total_tokens':3}}}),
                (4,'audit.completed',summary),(5,'run.completed',{})):
                session.add(AgentEventModel(run_id='run',sequence=sequence,event_type=kind,payload=payload,
                    created_at=now+timedelta(seconds=60 if sequence==5 else 0)))
            session.commit()
        return collect_run_metrics('run','alice',session_factory=self.sessions),recorder

    def test_confirmed_pre_model_rejection_has_separate_accounted_usage_and_adjusted_time(self):
        metrics,recorder=self.simulate()
        self.assertEqual('partial',metrics['usage_status'])
        self.assertEqual('complete_with_declared_zero_rejections',metrics['accounted_usage_status'])
        self.assertEqual(1,metrics['assumed_zero_token_attempts'])
        self.assertEqual(0,metrics['unknown_cost_http_attempts'])
        self.assertEqual(3,metrics['actual_total_tokens'])
        self.assertEqual(60000,metrics['duration_ms'])
        self.assertEqual(37125,metrics['excluded_network_ms'])
        self.assertEqual(22875,metrics['adjusted_duration_ms'])
        failure=next(row['payload'] for row in self.repository.read('run','alice')['records'] if row['kind']=='request_failed')
        self.assertEqual('not_started_operator_confirmed',failure['model_execution'])

    def test_unconfirmed_gateway_and_timeout_failures_keep_unknown_cost_and_wall_time(self):
        metrics,_=self.simulate(status=504)
        self.assertEqual(0,metrics['assumed_zero_token_attempts'])
        self.assertEqual(1,metrics['unknown_cost_http_attempts'])
        self.assertEqual('partial',metrics['accounted_usage_status'])
        self.assertEqual(metrics['duration_ms'],metrics['adjusted_duration_ms'])

    def test_no_endpoint_declaration_does_not_infer_free_rejection(self):
        metrics,_=self.simulate(declared=False)
        self.assertEqual(0,metrics['assumed_zero_token_attempts'])
        self.assertEqual(0,metrics['excluded_network_ms'])

    def test_timeout_with_same_numeric_status_is_not_a_confirmed_http_rejection(self):
        metrics,_=self.simulate(status=503,error_type='TimeoutError')
        self.assertEqual(0,metrics['assumed_zero_token_attempts'])

    def test_changed_endpoint_model_or_provider_invalidates_declaration(self):
        os.environ['CODE_EXPLORER_LLM_PRE_MODEL_REJECTION_ENDPOINT_SHA256']=endpoint_accounting_fingerprint()
        self.assertEqual([429,503],pre_model_rejection_policy()['confirmed_pre_model_statuses'])
        self.assertEqual([],pre_model_rejection_policy('another-model')['confirmed_pre_model_statuses'])
        with patch.dict(os.environ,{'CODE_EXPLORER_LLM_BASE_URL':'https://other.invalid/v1'}):
            self.assertEqual([],pre_model_rejection_policy()['confirmed_pre_model_statuses'])
        with patch.dict(os.environ,{'CODE_EXPLORER_LLM_PROVIDER':'vllm'}):
            self.assertEqual([],pre_model_rejection_policy()['confirmed_pre_model_statuses'])

    def test_legacy_metrics_do_not_use_current_environment_to_backfill(self):
        os.environ['CODE_EXPLORER_LLM_PRE_MODEL_REJECTION_ENDPOINT_SHA256']=endpoint_accounting_fingerprint()
        metrics=collect_run_metrics('run','alice',session_factory=self.sessions)
        self.assertIsNone(metrics['adjusted_duration_ms'])
        self.assertIsNone(metrics['network_accounting'])

    def test_a_run_with_only_confirmed_rejections_can_account_zero_but_has_no_provider_usage(self):
        os.environ['CODE_EXPLORER_LLM_PRE_MODEL_REJECTION_ENDPOINT_SHA256']=endpoint_accounting_fingerprint()
        recorder=AuditRecorder(self.repository,'run','alice')
        with tempfile.TemporaryDirectory() as directory:
            recorder.start(Path(directory),{},'i',[],'v',{'network_accounting':pre_model_rejection_policy()})
        recorder.planned({'messages':[]},1)
        identifier=recorder.sent({'messages':[]},1)
        recorder.failed(identifier,'HTTPError',diagnostics={'attempt':1,'status_code':503,'elapsed_ms':500})
        summary=recorder.finish()
        with self.sessions() as session:
            session.add_all([AgentEventModel(run_id='run',sequence=1,event_type='model.started',payload={}),
                AgentEventModel(run_id='run',sequence=2,event_type='audit.completed',payload=summary)])
            session.commit()
        metrics=collect_run_metrics('run','alice',session_factory=self.sessions)
        self.assertEqual('unavailable',metrics['usage_status'])
        self.assertIsNone(metrics['actual_total_tokens'])
        self.assertEqual(0,metrics['accounted_model_total_tokens'])
        self.assertEqual('complete_with_declared_zero_rejections',metrics['accounted_usage_status'])

    def test_missing_or_invalid_failure_timing_does_not_fabricate_an_adjusted_duration(self):
        recorder=AuditRecorder(self.repository,'run','alice')
        recorder.network_policy={'basis':'operator_endpoint_declaration','confirmed_pre_model_statuses':[503]}
        identifier=recorder.sent({'messages':[]},1)
        recorder.failed(identifier,'HTTPError',diagnostics={'attempt':1,'status_code':503})
        self.assertEqual(1,recorder.unknown_rejection_timings)
        self.assertEqual(0,recorder.excluded_request_ms)
        self.assertEqual(1,len(recorder.rejected_requests))

    def test_retry_wait_is_bound_to_failed_step_and_attempt_and_is_not_double_counted(self):
        recorder=AuditRecorder(self.repository,'run','alice')
        recorder.network_policy={'basis':'operator_endpoint_declaration','confirmed_pre_model_statuses':[429]}
        recorder.planned({},1);identifier=recorder.sent({'messages':[]},1)
        recorder.failed(identifier,'HTTPError',diagnostics={'attempt':1,'status_code':429,'elapsed_ms':1})
        recorder.retry_wait(2,30,30,status_code=429,completed=True)
        self.assertEqual(0,recorder.excluded_wait_ms)
        recorder.retry_wait(1,30,12,status_code=429,completed=False)
        recorder.retry_wait(1,30,12,status_code=429,completed=False)
        self.assertEqual(12000,recorder.excluded_wait_ms)


if __name__=='__main__':unittest.main()
