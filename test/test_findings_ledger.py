"""通用正反例验证状态计数与覆盖，不提供真实实验项目答案，不调用模型。"""

import copy
import json
import unittest

from backend.app.experiments.findings import FindingLedger, parse_ledger
from backend.app.experiments.report import REPORT_INSTRUCTIONS, assess_answer
from backend.app.llm.response_metadata import ModelResponseMetadata
from backend.app.llm.base import ModelTurn, ModelOutputError
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.batching import AnalysisBatches
from test.test_agent_foundation import MemoryRunStore
from test.test_on_demand_evidence import LEDGER_JSON, STOP


def finding(status='source_supported_risk', name='输入未经边界约束进入危险实现'):
    return {'root_cause':name, 'behavior':'包装层把参数传给已读取的危险实现。','behavior_status':'observed',
        'chain':{'status':'closed','description':'入口参数→包装层→真实危险实现。'},
        'protections':[], 'impact':{'status':'conditional','description':'影响依赖部署与攻击者可达性。'},
        'confirmed_conditions':['源码中存在调用方。'],'unresolved_conditions':['实际暴露面未知。'],
        'evidence':[{'path':'app.py','start_line':1,'end_line':2}], 'status':status,
        'exclusion_reason':'已读取实现使用固定参数且不经 shell。' if status=='excluded' else ''}


class FindingsLedgerTests(unittest.TestCase):
    def ledger(self, *items):
        raw=json.loads(LEDGER_JSON);raw['findings']=list(items)
        return raw

    def test_prompt_schema_preserves_every_runtime_validation_field(self):
        schema=json.JSONDecoder().raw_decode(REPORT_INSTRUCTIONS[REPORT_INSTRUCTIONS.index('{"$defs"'):])[0]

        def strip_display(value):
            if isinstance(value,dict):
                return {key:strip_display(item) for key,item in value.items()
                        if key not in {'title','description'} or not isinstance(item,str)}
            if isinstance(value,list):return [strip_display(item) for item in value]
            return value

        self.assertEqual(strip_display(FindingLedger.model_json_schema()),schema)
        guard=schema['$defs']['ProtectionCheck']
        self.assertIn('evidence',guard['required'])
        self.assertIn('description',guard['properties'])
        self.assertNotIn('location',guard['properties'])
        self.assertFalse(guard['additionalProperties'])
        self.assertIn('confirmed_conditions',schema['$defs']['Finding']['properties'])
        self.assertNotIn('confirmed_conditions',schema['$defs']['ImpactState']['properties'])

    def test_misnested_conditions_guard_locations_and_summary_note_are_rejected(self):
        item=finding();item['impact']['confirmed_conditions']=item.pop('confirmed_conditions')
        with self.assertRaises(ValueError):parse_ledger(json.dumps(self.ledger(item)))
        item=finding();item['protections']=[{'description':'检查对应值。','value_match':'unverified',
            'context_match':'unverified','before_operation':'verified','location':item['evidence'],'note':'不得替代 evidence'}]
        with self.assertRaises(ValueError):parse_ledger(json.dumps(self.ledger(item)))
        with self.assertRaises(ValueError):parse_ledger(json.dumps(self.ledger()|{'findings_summary_note':'自写的计数'}))

    def test_counts_numbering_and_sections_come_only_from_states(self):
        candidate=finding('unclosed_candidate','模板拼接的外部输入尚未建立')
        candidate['chain']['status']='open';candidate['impact']['status']='not_established'
        ledger=parse_ledger(json.dumps(self.ledger(finding(),candidate,finding('excluded','已核实的排除项'))))
        report=ledger.render()
        self.assertIn('源码支持的风险 1 项；未闭合候选 1 项；已排除 1 项',report)
        self.assertNotIn('共确认 3',report)
        self.assertNotIn('模板拼接',report.split('## 未确认风险')[0])
        self.assertIn('F2：模板拼接',report.split('## 未确认风险')[1])
        self.assertIn('F3：已核实的排除项',report.split('## 范围与局限')[1])
        result=assess_answer(report,STOP,require_report=True)
        self.assertEqual('complete',result['status']);self.assertEqual('not_proven',result['semantic_coverage'])

    def test_summary_limit_does_not_remove_other_independent_roots(self):
        report=parse_ledger(json.dumps(self.ledger(*(finding(name=f'独立根因{i}') for i in range(7))))).render()
        self.assertEqual(5,report.split('## 已核实问题')[0].count('- F'))
        self.assertIn('F7：独立根因6',report)
        self.assertIn('源码支持的风险 7 项',report)

    def test_open_chain_unknown_behavior_and_conditional_effect_are_not_silently_upgraded(self):
        for patch in ({'chain':{'status':'open','description':'未读取危险实现。'}},
                      {'behavior_status':'inferred'}, {'impact':{'status':'not_established','description':'未知。'}},
                      {'unresolved_conditions':[]}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                parse_ledger(json.dumps(self.ledger(finding()|patch)))
        with self.assertRaises(ValueError):parse_ledger(json.dumps(self.ledger(finding('excluded')|{'exclusion_reason':''})))

    def test_duplicate_literal_roots_and_model_supplied_counts_are_rejected(self):
        with self.assertRaises(ValueError):parse_ledger(json.dumps(self.ledger(finding(),finding())))
        with self.assertRaises(ValueError):parse_ledger(json.dumps(self.ledger(finding())|{'confirmed_count':9}))

    def test_coverage_cannot_omit_categories_or_claim_checks_without_evidence(self):
        raw=self.ledger();raw['coverage'].pop('authentication')
        with self.assertRaises(ValueError):parse_ledger(json.dumps(raw))
        for status in ('checked','not_applicable'):
            raw=self.ledger();raw['coverage']['output_contexts']['status']=status
            with self.assertRaises(ValueError):parse_ledger(json.dumps(raw))
        raw['coverage']['output_contexts']['evidence']=[{'path':'app.py','start_line':1,'end_line':2}]
        self.assertIn('输出上下文：不适用',parse_ledger(json.dumps(raw)).render())

    def test_valid_fields_do_not_prove_wrong_variable_sanitizer_semantics(self):
        item=finding()
        item['protections']=[{'description':'净化了另一个值，模型却声称本值受保护。',
            'value_match':'verified','context_match':'verified','before_operation':'verified','evidence':item['evidence']}]
        ledger=parse_ledger(json.dumps(self.ledger(item)))
        self.assertIn('净化了另一个值',ledger.render())
        self.assertEqual('not_proven',assess_answer(ledger.render(),STOP,require_report=True)['semantic_coverage'])

    def test_parser_accepts_one_json_fence_but_not_trailing_text_or_invalid_ranges(self):
        self.assertEqual(parse_ledger(LEDGER_JSON),parse_ledger('```json\n'+LEDGER_JSON+'\n```'))
        with self.assertRaises(ValueError):parse_ledger(LEDGER_JSON+' 另写一份摘要')
        with self.assertRaises(ValueError):parse_ledger('{"findings":[],"findings":[]}')
        for reference in ({'path':'../outside.py','start_line':1,'end_line':1},
                          {'path':'app.py','start_line':3,'end_line':1}):
            raw=self.ledger();raw['reviewed_ranges']=[reference]
            with self.assertRaises(ValueError):parse_ledger(json.dumps(raw))

    def test_raw_model_reply_is_unchanged_and_invalid_ledger_does_not_retry_or_publish(self):
        store=MemoryRunStore();manager=AgentRunManager(store=store)
        turn=ModelTurn(LEDGER_JSON,'fixture','fixture',metadata=STOP)
        answer=manager._accept_answer('r',turn,require_report=True)
        self.assertEqual(LEDGER_JSON,turn.text)
        self.assertEqual(parse_ledger(LEDGER_JSON).render(),answer)
        self.assertEqual(1,len([p for kind,p in store.events if kind=='findings.recorded']))
        state=AnalysisBatches(metadata=STOP,active_ids={'c1'},visible_ids={'c1'})
        before=copy.deepcopy(state)
        with self.assertRaises(ValueError):state.publish('{broken', ['c1'], [])
        self.assertEqual(before,state)
        state.metadata=ModelResponseMetadata()
        with self.assertRaises(ValueError):state.publish(LEDGER_JSON,['c1'],[])
        with self.assertRaises(ModelOutputError):manager._accept_answer('r',ModelTurn('旧自由报告','fixture','fixture',metadata=STOP),require_report=True)
        with self.assertRaises(ModelOutputError):manager._accept_answer('r',ModelTurn(LEDGER_JSON,'fixture','fixture',metadata=ModelResponseMetadata(finish_reason='length')),require_report=True)


if __name__=='__main__':unittest.main()
