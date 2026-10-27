"""普通跨函数预算的正反例；不引入递归、闭包或新的安全规则。"""

import unittest
from unittest.mock import patch

from test import test_instance_fields as fixtures
from backend.app.services.security_analysis.flow_analysis.program_graph import ProgramGraphSecurityFlowAnalyzer


class CallDepthBudgetTests(unittest.TestCase):
    """默认八层只扩展已有传播，不改变固定安全值或预算终止语义。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def source(self, depth: int, *, clean: bool = False) -> str:
        """构造可明确计数的线性业务调用链，源与危险实参分属不同函数。"""
        lines = ['import os', 'def route():', ' value=input()', ' f0(value)']
        for index in range(depth):
            action = 'os.system(value)' if index == depth - 1 else 'f' + str(index + 1) + '(value)'
            if clean and index == 2:
                action = 'f' + str(index + 1) + '("fixed")'
            lines.extend(['def f' + str(index) + '(value):', ' ' + action])
        return '\n'.join(lines) + '\n'

    def test_default_reaches_eighth_call_but_clean_argument_stops(self) -> None:
        for clean, expected in ((False, True), (True, False)):
            with self.subTest(clean=clean):
                pack = self.analyze({'app.py': self.source(8, clean=clean)})
                self.assertEqual(expected, bool(self.verified(pack, 'PY-SINK-SHELL')), pack.flow_diagnostics)

    def test_old_and_new_budget_are_both_bounded(self) -> None:
        for budget, depth, expected in ((4, 5, False), (8, 5, True), (8, 9, False)):
            with self.subTest(budget=budget, depth=depth):
                with patch('backend.app.services.security_analysis.service.ProgramGraphSecurityFlowAnalyzer',
                           side_effect=lambda **kwargs: ProgramGraphSecurityFlowAnalyzer(max_call_depth=budget, **kwargs)):
                    pack = self.analyze({'app.py': self.source(depth)})
                self.assertEqual(expected, bool(self.verified(pack, 'PY-SINK-SHELL')), pack.flow_diagnostics)
                if not expected:
                    self.assertGreater(pack.flow_diagnostics['counts'].get('call_depth_limit', 0), 0)
