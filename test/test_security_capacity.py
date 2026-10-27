"""事实持久化、分批候选查询和资源终止的离线回归；替身流不用于证明污点语义。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.app.services.program_graph import ProgramGraphArtifact
from backend.app.services.security_analysis.contracts import EvidenceLocation, StaticSecurityFact
from backend.app.services.security_analysis.rule_engine import SecurityScanResult
from backend.app.services.security_analysis.service import SecurityAnalysisService
from backend.app.services.value_binding.objects import ObjectFieldSlot


def fact(kind: str, index: int) -> StaticSecurityFact:
    """生成包装层用的有限端点，结构图只连接相同编号的 Source 与 Sink。"""
    return StaticSecurityFact(fact_id=f'{kind}{index}', fact_kind=kind, rule_id='capacity-fixture',
        category='fixture', name=kind, symbol=f'{kind}{index}', location=EvidenceLocation(path='app.py', line=1))


class SecurityCapacityTests(unittest.TestCase):
    def analyze(self, *, count=3, flow_error=None, graph_failures=None, **budgets):
        """仅验证组织/持久化层，不运行模型、不更改真实项目或记录。"""
        scan = SecurityScanResult(sources=[fact('source', i) for i in range(count)],
                                  sinks=[fact('sink', i) for i in range(count)])
        scanner = Mock()
        scanner.scan.return_value = scan
        graph = {'nodes': [], 'links': [{'source': f'source{i}', 'target': f'sink{i}', 'relation': 'calls'} for i in range(count)]}
        program = Mock()
        program.analyze.return_value = ProgramGraphArtifact(failures=graph_failures or [])
        flow = Mock()
        flow.analyze.return_value = []
        flow.analyze.side_effect = flow_error
        with TemporaryDirectory() as directory:
            Path(directory, 'app.py').write_text('pass\n', encoding='utf8')
            return SecurityAnalysisService(scanner=scanner, program_graph_service=program, flow_analyzer=flow,
                max_sources=1, max_sinks=1, **budgets).analyze(project_root=directory, dependency_graph=graph)

    def test_small_windows_do_not_remove_later_sources_or_sinks(self):
        pack = self.analyze()
        self.assertEqual(6, len(pack.facts))
        self.assertEqual({('source0','sink0'), ('source1','sink1'), ('source2','sink2')},
                         {(item.source_fact_id,item.sink_fact_id) for item in pack.candidates})
        self.assertTrue(pack.coverage.facts_complete)
        self.assertTrue(pack.coverage.candidate_search['windows_exhausted'])
        self.assertEqual(9, pack.coverage.candidate_search['queries'])
        self.assertEqual((3,3), (pack.coverage.candidate_source_count,pack.coverage.candidate_sink_count))

    def test_query_and_candidate_budgets_stop_work_but_keep_all_known_endpoints(self):
        for budgets, reason in (({'max_structural_queries':1}, 'query_budget'), ({'max_candidates':1}, 'candidate_budget')):
            with self.subTest(reason=reason):
                pack = self.analyze(**budgets)
                self.assertEqual(6, len(pack.facts))
                self.assertEqual(reason, pack.coverage.candidate_search['stop_reason'])
                self.assertFalse(pack.coverage.candidate_search['windows_exhausted'])
                self.assertLessEqual(pack.coverage.candidate_source_count,1)

    def test_time_budget_is_explicit_without_sleep(self):
        with patch('backend.app.services.security_analysis.candidate_search.time.monotonic', side_effect=[0,0,2]):
            pack = self.analyze(max_structural_seconds=1)
        self.assertEqual(6, len(pack.facts))
        self.assertEqual('time_budget', pack.coverage.candidate_search['stop_reason'])

    def test_storage_capacity_rejects_instead_of_saving_an_incomplete_fact_registry(self):
        with self.assertRaisesRegex(ValueError, '持久化条数安全上限'):
            self.analyze(max_fact_records=2)

    def test_flow_exception_is_not_a_completed_zero_flow_analysis(self):
        try:
            ObjectFieldSlot(allocation_id='allocation', field_name='nested.field')
        except ValueError as exc:
            error = exc
        pack = self.analyze(flow_error=error)
        self.assertFalse(pack.completed)
        self.assertEqual('failed', pack.coverage.dataflow_status)
        failure = next(item for item in pack.scan_failures if item['reason']=='program_graph_dataflow_error')
        self.assertEqual('dataflow',failure['stage'])
        self.assertEqual('field_name',failure['validation_errors'][0]['path'][0])
        self.assertNotIn('nested.field',str(failure))
        self.assertEqual(6,len(pack.facts))

    def test_function_partition_failures_remain_visible_when_other_partitions_complete(self):
        pack=self.analyze(graph_failures=[{'reason':'function_graph_error','path':'app.py','line':1}])
        self.assertTrue(pack.completed)
        self.assertEqual('partial',pack.coverage.dataflow_status)
        self.assertTrue(any(item.get('stage')=='program_graph' for item in pack.scan_failures))


if __name__ == '__main__':
    unittest.main()
