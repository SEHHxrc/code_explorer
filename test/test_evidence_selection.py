"""路径摘要：统一入口、三类优先级、范围隔离、完整步骤及无正文；不调用模型。"""

import copy
import unittest

from backend.app.services.security_analysis.contracts import (
    DataFlowEvidence, EvidenceLocation, FlowStepEvidence, PathEdgeEvidence,
    SecurityCandidate, SecurityEvidencePack, StaticSecurityFact,
)
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector, encode_navigation


def fact(identifier, kind, line, path='src/impl.py', category=None, name=None, **kwargs):
    return StaticSecurityFact(fact_id=identifier,fact_kind=kind,rule_id='fixture-rule',category=category or kind,
        name=name or kind,symbol=path+'::Implementation::handle',location=EvidenceLocation(path=path,line=line),**kwargs)


def fixture(language='python', extension='.py'):
    path='src/impl'+extension
    source=fact('source','source',2,path,trust_class='untrusted')
    sink=fact('sink','sink',9,path,severity='high',metadata={'value_flow':{'arguments':[0],'receiver_role':'none'}})
    guard=fact('guard','guard',4,path,name='if raw_value')
    sanitizer=fact('sanitizer','sanitizer',5,path,name='basename',metadata={'value_flow':{'result_role':'sanitized'}})
    flow=DataFlowEvidence(flow_id='flow',language=language,symbol=source.symbol,source_fact_id='source',sink_fact_id='sink',
        status='may_reach_sink',confidence='low',steps=[FlowStepEvidence(step_id='step',order=0,kind='assignment',
            symbol=source.symbol,location=EvidenceLocation(path=path,line=7),expression='value = raw_value',
            input_names=['raw_value'],output_names=['value'],certainty='may',provenance='inferred')])
    candidate=SecurityCandidate(candidate_id='candidate',rule_id='fixture-rule',category='file_write',severity='high',confidence='low',
        source_fact_id='source',sink_fact_id='sink',dataflow_id='flow',path_kind='intra_procedural_dataflow',
        dataflow_verified=True,guard_fact_ids=['guard'],sanitizer_fact_ids=['sanitizer'],limitations=['aliasing not proven'])
    return SecurityEvidencePack(facts={f.fact_id:f for f in [source,sink,guard,sanitizer]},dataflows={'flow':flow},
                                candidates=[candidate],languages_analyzed=[language],limitations=['not exhaustive'])


class EvidenceSelectionTests(unittest.TestCase):
    def assert_valid(self,result,budget):
        self.assertLessEqual(len(encode_navigation(result).encode('utf-8')),budget)
        omitted=result.get('omitted_items',result.get('omitted_paths'))
        self.assertEqual(result['total_matching']-len(result['items']),omitted)
        self.assertEqual(bool(omitted),result['has_more'])
        self.assertEqual(sorted(row['priority'] for row in result['items']),[row['priority'] for row in result['items']])

    def test_cross_language_path_keeps_roles_order_and_uncertainty_not_expressions(self):
        for language,extension in [('python','.py'),('java','.java'),('go','.go'),('javascript','.js'),('typescript','.ts'),('c','.c')]:
            pack=fixture(language,extension);before=pack.model_dump()
            selector=SecurityEvidenceSelector(pack)
            result=selector.build(details_available=False)
            self.assert_valid(result,8000)
            row=result['items'][0]
            self.assertEqual('propagation_path',row['kind'])
            self.assertEqual('may_reach_sink',row['value_flow'])
            self.assertEqual('inline_all_retained_steps',row['sequence_delivery'])
            self.assertEqual(['value'],row['sequence'][0]['defines'])
            self.assertEqual('may',row['sequence'][0]['certainty'])
            self.assertEqual({'arguments':[0]},row['sink']['parameter_roles'])
            self.assertNotIn('value = raw_value',encode_navigation(result))
            self.assertNotIn('protections',result)
            self.assertEqual(before,pack.model_dump())

    def test_summary_has_no_standalone_guard_source_or_global_coverages(self):
        pack=fixture()
        for index in range(100):pack.facts[str(index)]=fact(str(index),'guard',index+12)
        result=SecurityEvidenceSelector(pack).build()
        self.assertEqual(1,len(result['items']))
        self.assertEqual('path_group',result['items'][0]['kind'])
        for name in ('coverage','rule_packs','limitations','entrypoints','nodes','cards','snippets'):
            self.assertNotIn(name,result)
        self.assertNotIn('if raw_value',encode_navigation(result))

    def test_propagation_then_real_call_structure_then_shell_operation(self):
        pack=fixture()
        sink=fact('called','sink',20,'src/callee.py',severity='high')
        shell=fact('shell','sink',10,'src/runner.py',severity='high',metadata={'shell':True})
        pack.facts.update(called=sink,shell=shell)
        edge=PathEdgeEvidence(edge_id='edge',callsite_id='callsite',source=pack.facts['source'].symbol,
            target=sink.symbol,callsite=EvidenceLocation(path='src/impl.py',line=8))
        pack.call_edges['edge']=edge
        pack.candidates.append(pack.candidates[0].model_copy(update={'candidate_id':'structural','sink_fact_id':'called',
            'dataflow_id':None,'dataflow_verified':False,'call_edge_ids':['edge'],'path_kind':'structural_call_path'}))
        result=SecurityEvidenceSelector(pack).build()
        self.assertEqual(['path_group','path_group','operation_lead'],[row['kind'] for row in result['items']])
        self.assertEqual([1,2,3],[row['priority'] for row in result['items']])
        self.assertEqual({'structural_lead':1},result['items'][1]['route_kinds'])
        self.assert_valid(result,8000)

    def test_no_flow_cooccurrence_is_not_a_fabricated_call_or_taint_path(self):
        pack=fixture();pack.dataflows={};pack.candidates[0].dataflow_id=None
        row=SecurityEvidenceSelector(pack).rows()[0]
        self.assertEqual('operation_lead',row['kind'])
        self.assertEqual('same_function_only',row['relation'])
        self.assertEqual('not_established',row['path_status'])
        self.assertNotIn('sequence',row)

    def test_unpaired_operation_keeps_location_without_inventing_source(self):
        pack=fixture();pack.candidates=[];pack.dataflows={}
        row=SecurityEvidenceSelector(pack).build()['items'][0]
        self.assertEqual('sink',row['id'])
        self.assertEqual('single_operation',row['relation'])
        self.assertNotIn('source',row)
        self.assertEqual(9,row['sink']['line'])

    def test_zero_sink_has_no_filler_or_safe_conclusion(self):
        pack=fixture();pack.candidates=[];pack.dataflows={};pack.facts.pop('sink')
        result=SecurityEvidenceSelector(pack).build()
        self.assertEqual([],result['items'])
        self.assertEqual(0,result['total_matching'])
        self.assertEqual('_coverage',result['coverage_details_id'])
        self.assertIn('零条目不证明安全',result['semantics'])

    def test_long_path_retains_id_but_not_sampled_partial_sequence(self):
        pack=fixture()
        pack.dataflows['flow'].steps=[pack.dataflows['flow'].steps[0].model_copy(update={'step_id':f's{i}','order':i}) for i in range(200)]
        result=SecurityEvidenceSelector(pack).build(max_bytes=2500)
        self.assert_valid(result,2500)
        row=result['items'][0]
        self.assertEqual('path_group',row['kind'])
        self.assertEqual(1,row['retained_routes'])
        self.assertEqual('select_route_id',row['sequence_delivery'])
        self.assertNotIn('sequence',row)
        with self.assertRaises(ValueError):SecurityEvidenceSelector(pack).build(max_bytes=200)

    def test_unavailable_detail_tools_are_not_advertised_in_cumulative_mode(self):
        pack=fixture()
        pack.dataflows['flow'].steps=[pack.dataflows['flow'].steps[0].model_copy(update={'step_id':f's{i}','order':i}) for i in range(200)]
        result=SecurityEvidenceSelector(pack).build(max_bytes=2500,details_available=False)
        self.assertFalse(result['details_available'])
        self.assertNotIn('coverage_details_id',result)
        self.assertEqual('not_delivered',result['items'][0]['sequence_delivery'])

    def test_complete_high_priority_path_is_not_starved_by_lower_priority_locators(self):
        pack=fixture()
        for i in range(30):pack.facts[f'op{i}']=fact(f'op{i}','sink',10+i,f'src/file{i}.py')
        result=SecurityEvidenceSelector(pack).build(max_bytes=4000)
        self.assertEqual('select_route_id',result['items'][0]['sequence_delivery'])
        self.assertEqual('path_group',result['items'][0]['kind'])

    def test_extremely_long_locator_keeps_high_priority_id_for_detail(self):
        pack=fixture();pack.facts['source'].name='source-name'*2000
        row=SecurityEvidenceSelector(pack).build(max_bytes=2500)['items'][0]
        self.assertTrue(row['id'].startswith('pathgroup:'))
        self.assertEqual('detail_only',row['locators_delivery'])
        self.assertEqual('source',row['source_ref'])
        self.assertNotIn('source',row)
        self.assertNotIn('sequence',row)

    def test_outside_scope_path_is_not_exposed_and_missing_endpoint_is_counted(self):
        pack=fixture();pack.dataflows['flow'].steps[0].location.path='outside/private.py'
        result=SecurityEvidenceSelector(pack,{'src/impl.py'}).build()
        self.assertEqual({'outside_scope':1},result['items'][0]['route_statuses'])
        self.assertNotIn('outside/private.py',encode_navigation(result))
        pack=fixture();pack.facts.pop('source')
        result=SecurityEvidenceSelector(pack).build()
        self.assertEqual(1,result['unavailable_candidates'])
        self.assertEqual('operation_lead',result['items'][0]['kind'])

    def test_wrong_identity_or_flow_binding_fails_before_delivery(self):
        pack=fixture();pack.dataflows['flow'].source_fact_id='wrong'
        with self.assertRaises(ValueError):SecurityEvidenceSelector(pack)
        pack=fixture();pack.facts['bad']=pack.facts.pop('source')
        with self.assertRaises(ValueError):SecurityEvidenceSelector(pack)

    def test_sequence_and_endpoint_preserve_exact_coordinates_and_symbols(self):
        pack=fixture()
        pack.facts['sink'].symbol='package::Worker::handle'
        pack.dataflows['flow'].steps[0].symbol='package::Worker::handle'
        for point in (pack.facts['sink'].location,pack.dataflows['flow'].steps[0].location):
            point.column=8;point.end_line=11;point.end_column=19
        selector=SecurityEvidenceSelector(pack)
        row=selector.build()['items'][0]
        for point in (row['sink'],selector.sequence('candidate')[0]):
            self.assertEqual((8,11,19),(point['column'],point['end_line'],point['end_column']))
            self.assertEqual('package::Worker::handle',point['symbol'])

    def test_order_and_snapshot_are_independent_of_mapping_insertion_order(self):
        pack=fixture();other=copy.deepcopy(pack);other.facts=dict(reversed(list(other.facts.items())))
        self.assertEqual(SecurityEvidenceSelector(pack).build(),SecurityEvidenceSelector(other).build())

    def test_excluded_candidate_does_not_reappear_as_an_unpaired_sink(self):
        result=SecurityEvidenceSelector(fixture()).build(excluded={'candidate'})
        self.assertEqual([],result['items'])

    def test_unresolved_call_reference_does_not_claim_complete_path(self):
        pack=fixture();pack.dataflows={};pack.candidates[0].call_edge_ids=['missing-edge']
        row=SecurityEvidenceSelector(pack).build()['items'][0]
        self.assertEqual({'incomplete':1},row['route_statuses'])
        self.assertTrue(row['analysis_truncated'])


if __name__=='__main__':unittest.main()
