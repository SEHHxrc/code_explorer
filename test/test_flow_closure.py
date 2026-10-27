"""参数/返回、字段值和 finally 的公共闭合回归；只解析，不执行危险操作。"""

import unittest
import json
from unittest.mock import patch

from test import test_condition_routes as route_fixtures
from test.test_control_dependence import _GraphFixture
from backend.app.services.program_graph.path_conditions import PathConditionQuery
from backend.app.services.security_analysis.flow_analysis.call_graph import ProgramArgumentBinder


class FlowClosureTests(unittest.TestCase):
    """核对值语义及正反条件，不以候选数量代替分析正确性。"""

    analyze = route_fixtures.ConditionRouteTests.analyze
    python = route_fixtures.ConditionRouteTests.python
    route_proofs = route_fixtures.ConditionRouteTests.route_proofs

    def test_omitted_literal_default_and_explicit_override(self) -> None:
        """只对真正省略的参数应用字面量默认值，显式实参优先。"""
        for arguments, expected in (("value", "infeasible_under_model"), ("value,True", "satisfiable_under_model")):
            pack = self.python("def worker(command,enabled=False):\n if enabled:\n  os.system(command)\n", f"worker({arguments})")
            proofs = self.route_proofs(pack)
            self.assertTrue(proofs)
            self.assertEqual(expected, proofs[0].status, proofs[0])

    def test_aggregate_return_keeps_untainted_condition_component(self) -> None:
        """返回的第二个布尔分量虽无污点，仍约束第一个分量的危险使用。"""
        for receive, condition, payload in (("command,enabled", "enabled", "command"), ("pair", "pair[1]", "pair[0]")):
            pack = self.python("def wrap(command):\n return command,False\n", f"{receive}=wrap(value)\nif {condition}:\n os.system({payload})")
            proofs = self.route_proofs(pack)
            self.assertTrue(proofs, pack.flow_diagnostics)
            self.assertEqual("infeasible_under_model", proofs[0].status, proofs[0])

    def test_finally_freezes_pending_return(self) -> None:
        """finally 覆写局部变量不能改变已求值的返回对象。"""
        pack = self.python("def wrap(command):\n try:\n  return command\n finally:\n  command='fixed'\n", "result=wrap(value)\nos.system(result)")
        self.assertTrue(any(item.dataflow_verified for item in pack.candidates), pack.flow_diagnostics)

    def test_collection_writeback_composes_sibling_guard(self) -> None:
        """确定元素写回必须带回同次写入的布尔成员，不能只传污点元素。"""
        for enabled, expected in (("False", "infeasible_under_model"), ("True", "satisfiable_under_model")):
            pack = self.python(f"def write(box,command):\n box['command']=command\n box['enabled']={enabled}\n",
                "box={'command':'fixed','enabled':False}\nwrite(box,value)\nif box['enabled']:\n os.system(box['command'])")
            proofs = self.route_proofs(pack)
            self.assertTrue(proofs, pack.flow_diagnostics)
            self.assertEqual(expected, proofs[0].status, proofs[0])

    def test_finally_return_overrides_pending_return(self) -> None:
        """finally 自己返回固定值时，不允许被覆盖的旧 return 到达调用点。"""
        pack = self.python("def wrap(command):\n try:\n  return command\n finally:\n  return 'fixed'\n", "result=wrap(value)\nos.system(result)")
        self.assertFalse(any(item.dataflow_verified for item in pack.candidates), pack.flow_diagnostics)

    def test_mapped_receiver_is_not_an_omitted_argument(self) -> None:
        """已解析实例方法使用公共接收者契约，不因 self 被显式参数绑定器排除而污染字段证明。"""
        helper = "class Box:\n def __init__(self):\n  self.command='fixed'\n  self.enabled=True\n def fill(self,command,enabled):\n  self.command=command\n  self.enabled=enabled\n"
        for enabled, expected in (("False", "infeasible_under_model"), ("True", "satisfiable_under_model")):
            proofs = self.route_proofs(self.python(helper, f"box=Box()\nbox.fill(value,{enabled})\nif box.enabled:\n os.system(box.command)"))
            self.assertTrue(proofs)
            self.assertEqual(expected, proofs[0].status, proofs[0])

    def test_receiver_truth_is_not_proved_as_a_primitive(self) -> None:
        """接收者已绑定不等于知道自定义 __bool__，不能用修复字段证明来猜对象真值。"""
        helper = "class Box:\n def __init__(self):\n  self.command='fixed'\n def fill(self,command):\n  if self:\n   self.command=command\n"
        proofs = self.route_proofs(self.python(helper, "box=Box()\nbox.fill(value)\nos.system(box.command)"))
        self.assertTrue(proofs)
        self.assertEqual("unknown", proofs[0].status, proofs[0])
        self.assertTrue(any("condition_receiver_object_not_scalar" in proof.reasons for proof in proofs))

    def test_nested_finally_and_call_return_keep_original_value(self) -> None:
        """嵌套清理和 return 调用结果复用快照，不能在恢复时重算原表达式。"""
        helpers = (
            "def wrap(command):\n try:\n  try:\n   return command\n  finally:\n   command='first'\n finally:\n  command='second'\n",
            "def echo(command):\n return command\ndef wrap(command):\n try:\n  return echo(command)\n finally:\n  command='fixed'\n",
        )
        for helper in helpers:
            pack = self.python(helper, "result=wrap(value)\nos.system(result)")
            self.assertTrue(any(item.dataflow_verified for item in pack.candidates), pack.flow_diagnostics)

    def test_typed_handler_retains_unhandled_exception(self) -> None:
        """类型化 catch 不假装匹配全部异常；只有明确 catch-all 可消除未处理出口。"""
        for handler, expected in (("except ValueError:", True), ("except:", False)):
            graph = _GraphFixture.graph(self, f"def route():\n try:\n  helper()\n {handler}\n  pass\n", "app.py")
            function = next(item for item in graph.functions.values() if item.name == "route")
            call = next(node for node in function.nodes.values() if node.calls)
            self.assertEqual(expected, any(edge.kind == "cfg" and edge.source == call.node_id and edge.target == function.exit_node_id
                and edge.branch == "exception" for edge in function.edges))

    def test_unknown_side_effect_does_not_certify_old_guard(self) -> None:
        """未选中的调用可能改变条件值，不能将调用前 flag=False 误证明为后续不可达。"""
        graph = _GraphFixture.graph(self, "def route(value):\n flag=False\n unknown()\n if flag:\n  sink(value)\n", "app.py")
        function = next(item for item in graph.functions.values() if item.name == "route")
        sink = next(node for node in function.nodes.values() if any(call.name == "sink" for call in node.calls))
        proof = PathConditionQuery().prove(function, [sink.node_id])
        self.assertEqual("unknown", proof.status, proof)
        self.assertIn("call_side_effects_not_modelled", proof.reasons)

    def test_finally_updates_normal_break_and_continue(self) -> None:
        """正常、break/continue 三种完成原因均经过 finally，不能跳过防护标量更新。"""
        sources = (
            "def route(value):\n enabled=True\n try:\n  command=value\n finally:\n  enabled=False\n if enabled:\n  sink(command)\n",
            "def route(value):\n enabled=True\n while value:\n  try:\n   break\n  finally:\n   enabled=False\n if enabled and value:\n  sink(value)\n",
            "def route(value):\n enabled=True\n while enabled:\n  try:\n   continue\n  finally:\n   enabled=False\n if enabled:\n  sink(value)\n",
        )
        for index, source in enumerate(sources):
            graph = _GraphFixture.graph(self, source, "app.py")
            function = next(item for item in graph.functions.values() if item.name == "route")
            sink = next(node for node in function.nodes.values() if any(call.name == "sink" for call in node.calls))
            proof = PathConditionQuery().prove(function, [sink.node_id])
            self.assertEqual("unknown" if index == 2 else "infeasible_under_model", proof.status, proof)
            if index == 2:
                self.assertIn("repeated_node_requires_loop_invariant", proof.reasons)

    def test_caught_call_failure_does_not_write_its_result(self) -> None:
        """失败的 RHS 调用不会生成新的接收值，异常边保留调用前 x 的到达定义。"""
        graph = _GraphFixture.graph(self, "def route(value):\n command=value\n try:\n  command=helper()\n except:\n  sink(command)\n", "app.py")
        function = next(item for item in graph.functions.values() if item.name == "route")
        sink = next(node for node in function.nodes.values() if any(call.name == "sink" for call in node.calls))
        predecessors = [function.nodes[edge.source] for edge in function.edges if edge.kind == "reaching_def" and edge.target == sink.node_id and edge.variable == "command"]
        self.assertTrue(predecessors)
        self.assertTrue(all("helper()" not in node.code for node in predecessors))

    def test_finally_normal_exit_keeps_its_branch_condition(self) -> None:
        """清理块末尾条件的 false 出口不能被当作无条件恢复。"""
        graph = _GraphFixture.graph(self, "def route(value):\n try:\n  command=value\n finally:\n  if value:\n   return\n if value:\n  sink(command)\n", "app.py")
        function = next(item for item in graph.functions.values() if item.name == "route")
        sink = next(node for node in function.nodes.values() if any(call.name == "sink" for call in node.calls))
        self.assertEqual("infeasible_under_model", PathConditionQuery().prove(function, [sink.node_id]).status)

    def test_common_frontends_keep_literal_defaults(self) -> None:
        """Python/JS/TS 语法不同但把可观察默认值交给同一参数绑定器。"""
        cases = {
            "app.py": "def worker(command,enabled=False):\n pass\ndef route(value):\n worker(value)\n",
            "app.js": "function worker(command,enabled=false){} function route(value){worker(value)}",
            "app.ts": "function worker(command:string,enabled:boolean=false){} function route(value:string){worker(value)}",
        }
        for filename, source in cases.items():
            graph = _GraphFixture.graph(self, source, filename)
            worker = next(item for item in graph.functions.values() if item.name == "worker")
            call = next(call for function in graph.functions.values() for node in function.nodes.values() for call in node.calls if call.name == "worker")
            self.assertIs(False, dict(ProgramArgumentBinder().bind_conditions(call, worker))["enabled"].value)
            spread = call.model_copy(update={"positional_spread_positions": [0]})
            self.assertNotIn("enabled", dict(ProgramArgumentBinder().bind_conditions(spread, worker)))

    def test_rewritten_fixtures_are_freshly_analyzed_and_delivered(self) -> None:
        """三套新源码走生产流水线，再验证 Source/Sink/条件和按需详情，不读取历史工件。"""
        from test.run_flow_closure_trial import FIXTURES, EXPECTATIONS, validate_pack
        from backend.app.services.project_analysis.pipeline import ProjectAnalysisPipeline
        specs = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["cases"]
        for case, spec in specs.items():
            root = FIXTURES / case
            self.assertEqual(["app.py"], sorted(path.name for path in root.rglob("*") if path.is_file()))
            bundle = ProjectAnalysisPipeline().analyze(str(root.resolve()))
            self.assertTrue(validate_pack(bundle.security_evidence, spec)["routes"])


class FlowClosureTrialSelectionTests(unittest.IsolatedAsyncioTestCase):
    """重测只选择完整配对；非法筛选必须在导入和调用模型之前失败。"""

    async def test_invalid_selection_does_not_start_analysis(self) -> None:
        """空、重复或未知样本均不能悄悄扩大到全部实验。"""
        from test.run_flow_closure_trial import run_trials
        for cases in ((), ("unknown",), ("parameter_return", "parameter_return")):
            with self.subTest(cases=cases), patch("test.run_flow_closure_trial.ProjectAnalysisPipeline") as pipeline:
                with self.assertRaises(ValueError):
                    await run_trials(run_model=True, case_ids=cases)
                pipeline.assert_not_called()

    async def test_offline_selection_records_transport_condition(self) -> None:
        """重新分析选中样本，保存传输阈值而不导入项目或发送真实模型请求。"""
        from test.run_flow_closure_trial import run_trials
        with patch("test.run_flow_closure_trial.ProjectImportService") as importer:
            result = await run_trials(run_model=False, case_ids=("parameter_return",), condition_label="idle-600")
        importer.assert_not_called()
        self.assertEqual(["parameter_return"], list(result["offline"]))
        self.assertEqual("idle-600", result["condition_label"])
        self.assertIn("stream_idle_timeout_seconds", result["transport"])


if __name__ == "__main__":
    unittest.main()
