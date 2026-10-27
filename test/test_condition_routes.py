"""既有跨过程能力的条件衔接回归；不请求真实模型，不为样例增加专用安全规则。"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend.app.services.program_graph.conditions import PathConditionProof
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.program_graph.path_composition import ConditionRoutePart, ConditionRouteQuery
from backend.app.services.security_analysis.evidence_query import SecurityEvidenceCatalog
from backend.app.services.security_analysis.flow_analysis.call_graph import ProgramArgumentBinder
from backend.app.services.security_analysis.flow_analysis.program_graph import ProgramGraphSecurityFlowAnalyzer
from test import test_instance_fields as fixtures
from test import test_control_dependence as graph_fixtures


class ConditionRouteTests(unittest.TestCase):
    """通过真实源码核对调用前条件、实际值绑定、返回恢复和未知边界。"""

    analyze = fixtures.InstanceFieldTests.analyze

    def python(self, helper: str, body: str):
        """网络 Source 使用既有 FastAPI 适配器；测试只变更应用源码。"""
        return self.analyze({"app.py": "from fastapi import FastAPI\nimport os\napp=FastAPI()\n" + helper +
            "\n@app.get('/')\ndef route(value:str):\n" + "".join(" " + line + "\n" for line in body.splitlines())})

    def route_proofs(self, pack) -> list[PathConditionProof]:
        """只取真实跨过程候选的组合核验，末段局部核验不能充当整条链路。"""
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures), pack.scan_failures)
        return [proof for flow in pack.dataflows.values() for proof in flow.path_conditions if proof.scope == "interprocedural"]

    def test_argument_constraints_cross_renamed_parameter(self) -> None:
        """调用前对 value 的条件必须约束 worker.command，不因函数或参数改名而丢失。"""
        for comparison, expected in (("!=", "infeasible_under_model"), ("==", "satisfiable_under_model")):
            with self.subTest(comparison=comparison):
                pack = self.python("def worker(command):\n if command" + comparison + "'a':\n  os.system(command)\n",
                                   "if value=='a':\n worker(value)")
                proofs = self.route_proofs(pack)
                self.assertTrue(proofs, pack.flow_diagnostics)
                self.assertEqual(expected, proofs[0].status, proofs[0])
                self.assertEqual(2, len(proofs[0].segments))
                self.assertEqual("command", proofs[0].segments[1].bindings[0].target_name)
                self.assertFalse(any("part:" in condition.node_id for condition in proofs[0].conditions))

    def test_return_resumes_same_caller_frame(self) -> None:
        """从实际返回恢复调用者，不能重跑其入口并丢掉调用前条件。"""
        for comparison, expected in (("!=", "infeasible_under_model"), ("==", "satisfiable_under_model")):
            with self.subTest(comparison=comparison):
                pack = self.python("def echo(command):\n return command\n", "if value=='a':\n result=echo(value)\n if result" + comparison + "'a':\n  os.system(result)")
                proofs = self.route_proofs(pack)
                self.assertTrue(proofs, pack.flow_diagnostics)
                self.assertEqual(expected, proofs[0].status, proofs[0])
                self.assertEqual(3, len(proofs[0].segments))
                self.assertEqual(proofs[0].segments[0].frame_id, proofs[0].segments[-1].frame_id)

    def test_transform_preserves_expression_not_identity(self) -> None:
        """拼接实参与返回保留真实表达式，不能因为只含一个变量而复制原值。"""
        for expected_value, expected in (("asuffix", "satisfiable_under_model"), ("a", "infeasible_under_model")):
            for helper, body in (
                (f"def worker(command):\n if command=='{expected_value}':\n  os.system(command)\n", "if value=='a':\n worker(value+'suffix')"),
                ("def echo(command):\n return command+'suffix'\n", f"if value=='a':\n result=echo(value)\n if result=='{expected_value}':\n  os.system(result)"),
            ):
                with self.subTest(body=body, expected=expected):
                    proofs = self.route_proofs(self.python(helper, body))
                    self.assertTrue(proofs)
                    self.assertEqual(expected, proofs[0].status, proofs[0])

    def test_bound_flag_and_nested_calls_are_composed(self) -> None:
        """不止跟踪污点形参：现有直接绑定的其他条件参数也必须传递。"""
        pack = self.python("def worker(command,flag):\n if flag:\n  os.system(command)\ndef relay(message,enabled):\n worker(message,enabled)\n",
                           "flag=False\nrelay(value,flag)")
        proofs = self.route_proofs(pack)
        self.assertTrue(proofs)
        self.assertEqual("infeasible_under_model", proofs[0].status, proofs[0])
        self.assertEqual(3, len(proofs[0].segments))
        for call in ("worker(value,False)", "worker(command=value,flag=False)"):
            pack = self.python("def worker(command,flag):\n if flag:\n  os.system(command)\n", call)
            proofs = self.route_proofs(pack)
            self.assertTrue(proofs)
            self.assertEqual("infeasible_under_model", proofs[0].status, proofs[0])

    def test_known_field_writeback_keeps_segments_and_bindings(self) -> None:
        """唯一对象的确定字段写回可组合，模型内 SAT 仍不等于证明真实利用。"""
        pack = self.python("class Box:\n def __init__(self):\n  self.command='fixed'\ndef write(box,command):\n box.command=command\n",
                           "box=Box()\nwrite(box,value)\nos.system(box.command)")
        proofs = self.route_proofs(pack)
        self.assertTrue(proofs, pack.flow_diagnostics)
        self.assertTrue(all(proof.segments for proof in proofs))
        self.assertTrue(any(proof.status == "satisfiable_under_model" for proof in proofs))
        self.assertTrue(any(segment.bindings for proof in proofs for segment in proof.segments))

    def test_evidence_pages_keep_bindings_and_no_control_graph(self) -> None:
        """摘要仍是候选路径，条件详情可分页恢复激活帧和绑定，不泄露临时组合 CFG。"""
        pack = self.python("def worker(command):\n if command!='a':\n  os.system(command)\n", "if value=='a':\n worker(value)")
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        candidate = next(item for item in pack.candidates if item.dataflow_id)
        values, offset = [], 0
        while True:
            page = catalog.detail(candidate.candidate_id, section="conditions", offset=offset, limit=1)
            values.extend(page["items"])
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        self.assertTrue(any(item["kind"] == "segment" for item in values))
        self.assertTrue(any(item["kind"] == "binding" for item in values))
        self.assertEqual(len(values), page["section_counts"]["conditions"])
        summary = catalog.summary(max_chars=8000)
        self.assertNotIn('"immediate_dominators"', summary)
        self.assertNotIn('"nodes"', summary)
        self.assertNotIn('"scalar_values"', summary)

    def test_contradictory_call_does_not_mask_another_existing_route(self) -> None:
        """同一 Source/Sink 的首条调用矛盾时，应继续核验已有备选调用并交付可满足的一条。"""
        pack = self.python("def worker(command):\n if command!='a':\n  os.system(command)\n",
                           "if value=='a':\n worker(value)\nworker(value)")
        proofs = self.route_proofs(pack)
        self.assertTrue(proofs)
        self.assertEqual("satisfiable_under_model", proofs[0].status, proofs[0])

    def test_alternative_limit_is_not_an_all_routes_proof(self) -> None:
        """备选核验超限明确标记截断，即使首条矛盾也不能宣称全部不可行。"""
        with patch("backend.app.services.security_analysis.service.ProgramGraphSecurityFlowAnalyzer",
                   side_effect=lambda **kwargs: ProgramGraphSecurityFlowAnalyzer(max_path_variants=1, **kwargs)):
            pack = self.python("def worker(command):\n if command!='a':\n  os.system(command)\n",
                               "if value=='a':\n worker(value)\nworker(value)")
        self.assertGreater(pack.flow_diagnostics["counts"].get("path_variant_limit", 0), 0)
        self.assertTrue(any(flow.truncated for flow in pack.dataflows.values()))

    def test_query_build_budget_is_visible(self) -> None:
        """不为大型跨过程路线无界复制图；构建预算耗尽也保留候选和 unknown。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text("def f(value):\n return value\n", encoding="utf8")
            function = next(iter(ProgramGraphService().analyze(directory).functions.values()))
        result = ConditionRouteQuery(max_nodes=1).prove((ConditionRoutePart(function=function, frame_id="root",
            start_node_id=function.entry_node_id, end_node_id=function.exit_node_id),))
        self.assertEqual("unknown", result.status)
        self.assertTrue(result.truncated)
        self.assertIn("condition_route_budget_exceeded", result.reasons)

    def test_literal_argument_contract_is_shared_across_languages(self) -> None:
        """Java/Go/JS/TS 的布尔字面量交给相同绑定入口，不靠变量名猜值。"""
        for filename, source in (
            ("App.java", 'class App { static void worker(String command,boolean flag){} static void route(){worker("x",false);} }'),
            ("app.go", 'package app\nfunc worker(command string,flag bool){}\nfunc route(){worker("x",false)}'),
            ("app.js", 'function worker(command,flag){} function route(){worker("x",false)}'),
            ("app.ts", 'function worker(command:string,flag:boolean){} function route(){worker("x",false)}'),
        ):
            with self.subTest(filename=filename):
                graph = graph_fixtures._GraphFixture.graph(self, source, filename)
                worker = next(function for function in graph.functions.values() if function.name == "worker")
                call = next(call for function in graph.functions.values() for node in function.nodes.values() for call in node.calls if call.name == "worker")
                values = dict(ProgramArgumentBinder().bind_literals(call, worker))
                self.assertIs(False, values["flag"].value)


if __name__ == "__main__":
    unittest.main()
