"""局部对象引用别名：真实源码端到端正反例，不运行项目或调用模型。"""

import unittest

from test import test_instance_fields as fixtures
from test import test_factory_returns as factory_fixtures
from test import test_value_identities as value_fixtures
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from backend.app.services.security_analysis.flow_analysis.object_references import ObjectReferenceIndex
from backend.app.services.security_analysis.flow_analysis.object_contracts import LocalInstance
from backend.app.services.security_analysis.flow_analysis.diagnostics import FlowDiagnostics
from backend.app.services.program_graph import ProgramGraphEdge


class ObjectReferenceTests(unittest.TestCase):
    """复用生产分析入口，验证同对象字段、变量重绑定、未知副作用和语言差异。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def python(self, body: str) -> SecurityEvidencePack:
        """将同一类定义与指定操作拼成临时项目，只执行静态分析。"""
        return self.analyze({"app.py": '''import os
class Worker:
 def __init__(self, value):
  self.command=value
  self.safe='fixed'
 def run(self):
  os.system(self.command)
 def reset(self):
  self.command='fixed'
def route():
 value=input()
''' + "".join(" " + line + "\n" for line in body.splitlines())})

    def test_python_alias_fields_and_rebinding_remain_distinct(self) -> None:
        """别名不复制对象，根变量重绑定不清除旧对象，且不会把整个对象当作污点。"""
        for body, expected in (
            ("a=Worker(value)\nb=a\nc=b\nos.system(c.command)", True),
            ("a=Worker(value)\nb=a\nos.system(b.safe)", False),
            ("a=Worker(value)\nb=a\na=Worker('fixed')\nos.system(b.command)", True),
            ("a=Worker(value)\nb=a\na=Worker('fixed')\nos.system(a.command)", False),
            ("a=Worker(value)\nb=a\nb=Worker('fixed')\nos.system(a.command)", True),
            ("a=Worker(value)\nb=a\nb=Worker('fixed')\nos.system(b.command)", False),
            ("a=Worker(value)\nb=a\nos.system(b)", False),
        ):
            with self.subTest(body=body):
                self.assertEqual(expected, bool(self.verified(self.python(body), "PY-SINK-SHELL")))

    def test_python_field_writes_are_shared_by_all_local_aliases(self) -> None:
        """别名两侧赋值写同一分配字段，常量覆盖不能残留旧污点。"""
        for body, expected in (
            ("a=Worker(value)\nb=a\nb.command='fixed'\nos.system(a.command)", False),
            ("a=Worker(value)\nb=a\na.command='fixed'\nos.system(b.command)", False),
            ("a=Worker('fixed')\nb=a\nb.command=value\nos.system(a.command)", True),
            ("a=Worker('fixed')\nb=a\nb.command=input()\nos.system(a.command)", True),
            ("a=Worker('fixed')\nb=a\na.command=value\nb.run()", True),
            ("a=Worker('fixed')\nb=a\nif flag:\n a.command=value\nb.run()", True),
            ("a=Worker(value)\nb=a\nb.safe='fixed'\nos.system(a.command)", True),
        ):
            with self.subTest(body=body):
                self.assertEqual(expected, bool(self.verified(self.python(body), "PY-SINK-SHELL")))

    def test_readonly_alias_method_keeps_real_alias_evidence(self) -> None:
        """方法目标仍来自既有调用图，证据中包含真实 b=a 位置及分配身份。"""
        pack = self.python("a=Worker(value)\nb=a\nb.run()")
        candidates = self.verified(pack, "PY-SINK-SHELL")
        self.assertTrue(candidates)
        steps = pack.dataflows[candidates[0].dataflow_id].steps
        self.assertTrue(any(step.expression == "b=a" and step.object_allocation_id for step in steps))
        restored = SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        catalog = fixtures.SecurityEvidenceCatalog(restored.model_dump())
        page = catalog.detail(candidates[0].candidate_id, section="flow", offset=0, limit=100)
        self.assertEqual([step.model_dump() for step in steps], page["items"])
        alias = next(step for step in steps if step.expression == "b=a")
        self.assertTrue(any(step["line"] == alias.location.line and step.get("object_allocation_id") == alias.object_allocation_id
                            for step in catalog.selector.sequence(candidates[0].candidate_id)))

        pack = self.python("a=Worker(value)\nb=a\nos.system(b.command)")
        candidates = self.verified(pack, "PY-SINK-SHELL")
        self.assertTrue(any(step.expression == "b=a" and step.object_allocation_id
                            for step in pack.dataflows[candidates[0].dataflow_id].steps))

    def test_factory_result_alias_and_cross_file_instance(self) -> None:
        """已支持工厂的调用结果可再建立局部别名，跨文件类仍只消费既有调用目标。"""
        pack = factory_fixtures.FactoryReturnTests().python("def make(value):\n return Worker(value)",
            "worker=make(value)\n alias=worker\n alias.run()")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
        pack = self.analyze({"worker.py": "import os\nclass Worker:\n def __init__(self,v):\n  self.command=v\n def run(self):\n  os.system(self.command)\n",
            "app.py": "from worker import Worker\ndef route():\n value=input()\n a=Worker(value)\n b=a\n b.run()\n"})
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))

    def test_alias_escape_mutation_and_branch_ambiguity_stay_explicit(self) -> None:
        """尚未实现的跨调用写回不借别名绕过拒绝，歧义分支不猜成唯一对象。"""
        for operation in ("unknown(b)", "b.unknown_method()"):
            with self.subTest(operation=operation):
                pack = self.python("a=Worker(value)\nb=a\n" + operation + "\na.run()")
                self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                self.assertTrue(pack.flow_diagnostics["counts"].get("instance_alias_escape_or_mutation_not_modelled"))
        pack = self.python("a=Worker(value)\nsafe=Worker('fixed')\nif flag:\n b=a\nelse:\n b=safe\nos.system(b.command)")
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(pack.flow_diagnostics["counts"].get("instance_alias_not_resolved"))

    def test_java_javascript_and_typescript_share_alias_field_logic(self) -> None:
        """只有语言适配声明引用语义，其余算法与 Python 共用；字段和安全兄弟项隔离。"""
        cases = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker {String command;String safe;Worker(String v){this.command=v;this.safe="fixed";}void run(){Runtime.getRuntime().exec(this.SELECTED);}static void route(){String value=System.getenv("CMD");Worker a=new Worker(INITIAL);Worker b=a;MUTATION b.run();}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v){this.command=v;this.safe='fixed';}run(){exec(this.SELECTED);}}function route(){const value=process.env.CMD;let a=new Worker(INITIAL);let b=a;MUTATION b.run();}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v:string){this.command=v;this.safe='fixed';}run(){exec(this.SELECTED);}}function route(){const value=process.env.CMD;let a=new Worker(INITIAL);let b=a;MUTATION b.run();}"),
        )
        for path, rule, source in cases:
            for field, initial, mutation, expected in (
                ("command", "value", "", True),
                ("safe", "value", "", False),
                ("command", "value", 'a.command="fixed";', False),
                ("command", "value", 'b.command="fixed";', False),
                ("command", "value", 'a=new Worker("fixed");', True),
                ("command", "value", 'b=new Worker("fixed");', False),
                ("command", '"fixed"', "a.command=value;", True),
                ("command", '"fixed"', "if(value != null){a.command=value;}", True),
            ):
                with self.subTest(path=path, field=field, initial=initial, mutation=mutation):
                    content = source.replace("SELECTED", field).replace("INITIAL", initial).replace("MUTATION", mutation)
                    self.assertEqual(expected, bool(self.verified(self.analyze({path: content}), rule)))


class ObjectReferenceContractTests(unittest.TestCase):
    """能力声明与预算边界独立于语言工具，也不能悄悄退化为部分精确结果。"""

    def test_value_copy_or_legacy_unknown_does_not_become_reference_alias(self) -> None:
        """未知历史产物、Go 结构体式值复制不可套用引用复制，只有明确声明才启用。"""
        for policy, expected in (("reference", True), ("value", False), ("unknown", False)):
            with self.subTest(policy=policy):
                graph = value_fixtures.ValueIdentityTests().graph(inner_reads_outer=True).model_copy(update={"object_assignment": policy})
                before = graph.model_dump()
                instance = LocalInstance("outer", "call", "allocation:test", "Worker", "value", ("command",))
                index = ObjectReferenceIndex(graph, [instance])
                self.assertEqual(expected, bool(index.possible(graph.nodes["inner_sink"], "value")))
                self.assertEqual(before, graph.model_dump())

    def test_budget_failure_does_not_return_a_partial_precise_alias_map(self) -> None:
        """超限后全部查询回到明确未知，并给出有界预算诊断。"""
        graph = value_fixtures.ValueIdentityTests().graph(inner_reads_outer=True).model_copy(update={"object_assignment": "reference"})
        instance = LocalInstance("outer", "call", "allocation:test", "Worker", "value", ("command",))
        diagnostics = FlowDiagnostics()
        index = ObjectReferenceIndex(graph, [instance], diagnostics, max_updates=0)
        self.assertFalse(index.available)
        self.assertFalse(index.possible(graph.nodes["inner_sink"], "value"))
        self.assertEqual(1, diagnostics.counts["instance_alias_budget_exceeded"])

    def test_unvisited_loop_backedge_is_not_an_unknown_execution_branch(self) -> None:
        """先计算到的循环回边为空不表示真实未初始化，不应丢掉循环外已知对象身份。"""
        graph = value_fixtures.ValueIdentityTests().graph(inner_reads_outer=True).model_copy(update={"object_assignment": "reference"})
        graph = graph.model_copy(update={"edges": [*graph.edges, ProgramGraphEdge(
            edge_id="loop", source="inner_sink", target="inner", kind="cfg", branch="back")]})
        instance = LocalInstance("outer", "call", "allocation:test", "Worker", "value", ("command",))
        index = ObjectReferenceIndex(graph, [instance])
        self.assertTrue(index.available)
        self.assertTrue(index.possible(graph.nodes["inner_sink"], "value"))

    def test_repeated_allocation_site_is_not_one_unique_runtime_instance(self) -> None:
        """循环内分配位置可产生多个对象，不能强更新为同一个确定共享实例。"""
        graph = value_fixtures.ValueIdentityTests().graph(inner_reads_outer=True).model_copy(update={"object_assignment": "reference"})
        graph = graph.model_copy(update={"edges": [*graph.edges, ProgramGraphEdge(
            edge_id="repeat", source="inner_sink", target="outer", kind="cfg", branch="back")]})
        instance = LocalInstance("outer", "call", "allocation:test", "Worker", "value", ("command",))
        diagnostics = FlowDiagnostics()
        index = ObjectReferenceIndex(graph, [instance], diagnostics)
        self.assertFalse(index.possible(graph.nodes["inner_sink"], "value"))
        self.assertTrue(any(item.get("reason_detail") == "allocation_site_may_repeat_in_cycle" for item in diagnostics.items))


if __name__ == "__main__":
    unittest.main()
