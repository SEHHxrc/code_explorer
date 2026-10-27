"""多类型工厂的公共字段/方法对应回归，不执行样例源码。"""

import unittest
from unittest.mock import patch

from test import test_instance_fields


class PolymorphicFactoryTests(unittest.TestCase):
    """同名字段必须保留实际类型，不能将 A 的输入状态绑定到 B 的敏感方法。"""

    analyze = test_instance_fields.InstanceFieldTests.analyze
    verified = test_instance_fields.InstanceFieldTests.verified

    def python(self, sink: str, route: str = "worker=make(value)\n worker.run()", factory: str = "") -> test_instance_fields.SecurityEvidencePack:
        """返回明确基类的两种实例，只有 Unsafe 的 command 来自输入。"""
        source = "import os\nclass Base:\n def run(self):\n  pass\nclass Unsafe(Base):\n def __init__(self,value):\n  self.command=value\n def run(self):\n  " + ("os.system(self.command)" if sink == "unsafe" else "pass") + "\nclass Safe(Base):\n def __init__(self,value):\n  self.command='fixed'\n def run(self):\n  " + ("os.system(self.command)" if sink == "safe" else "pass") + "\ndef make(value)->Base:\n" + (factory or " if flag:\n  return Unsafe(value)\n return Safe(value)") + "\ndef route():\n value=input()\n " + route + "\n"
        return self.analyze({"app.py": source})

    def test_python_actual_type_selects_compatible_method(self) -> None:
        """Unsafe 可到达自身敏感参数，Safe 的同名安全字段不继承 Unsafe 污点。"""
        for sink, expected in (("unsafe", True), ("safe", False)):
            with self.subTest(sink=sink):
                pack = self.python(sink)
                candidates = self.verified(pack, "PY-SINK-SHELL")
                self.assertEqual(expected, bool(candidates), pack.flow_diagnostics)
                if candidates:
                    flow = pack.dataflows[candidates[0].dataflow_id]
                    self.assertEqual({"app.py::Unsafe"}, {step.object_type_symbol for step in flow.steps if step.object_type_symbol})

    def test_forwarded_variants_keep_field_isolation(self) -> None:
        """多层工厂仍按具体类型导出字段，不能从聚合字段反向污染兄弟类型槽位。"""
        factory = " return inner(value)\ndef inner(value)->Base:\n if flag:\n  return Unsafe(value)\n return Safe(value)"
        for sink, expected in (("unsafe", True), ("safe", False)):
            with self.subTest(sink=sink):
                self.assertEqual(expected, bool(self.verified(self.python(sink, factory=factory), "PY-SINK-SHELL")))

    def test_call_context_reassignment_and_field_updates(self) -> None:
        """调用点与父字段覆盖仍有效；整对象重绑定不能继承旧实例类型。"""
        for route, sink, expected in (
            ("first=make(value)\n worker=make('fixed')\n worker.run()", "unsafe", False),
            ("worker=make(value)\n worker.command='fixed'\n worker.run()", "unsafe", False),
            ("worker=make(value)\n worker=make('fixed')\n worker.run()", "unsafe", False),
            ("worker=make(value)\n worker=input()\n worker.run()", "unsafe", False),
            ("worker=make('fixed')\n worker.command=value\n worker.run()", "safe", True),
        ):
            with self.subTest(route=route):
                self.assertEqual(expected, bool(self.verified(self.python(sink, route=route), "PY-SINK-SHELL")))

    def test_uninitialized_field_does_not_read_other_type_value(self) -> None:
        """A 未初始化的字段也有独立空槽，不能通过父路径回退读到 B 的同名字段。"""
        source = "import os\nclass Base:\n def run(self):\n  pass\nclass A(Base):\n def __init__(self,value):\n  self.safe='fixed'\n def run(self):\n  os.system(self.command)\nclass B(Base):\n def __init__(self,value):\n  self.command=value\n def run(self):\n  pass\ndef make(value)->Base:\n if flag:\n  return A(value)\n return B(value)\ndef route():\n value=input()\n worker=make(value)\n worker.run()\n"
        self.assertFalse(self.verified(self.analyze({"app.py": source}), "PY-SINK-SHELL"))

    def test_polymorphic_overlay_does_not_mutate_shared_graph(self) -> None:
        """类型槽位与方法字段绑定仅存在于消费者私有视图，不污染持久化程序图。"""
        captured = []
        original = test_instance_fields.ProgramGraphService.analyze

        def observe(service, *args, **kwargs):
            """捕获真实生产者返回对象和其原始序列化，不替换分析结果。"""
            graph = original(service, *args, **kwargs)
            captured.append((graph, graph.model_dump_json()))
            return graph

        with patch.object(test_instance_fields.ProgramGraphService, "analyze", autospec=True, side_effect=observe):
            self.assertTrue(self.verified(self.python("unsafe"), "PY-SINK-SHELL"))
        self.assertTrue(captured)
        self.assertTrue(all(graph.model_dump_json() == before for graph, before in captured))

    def test_type_metadata_survives_catalog_and_navigation(self) -> None:
        """模型按 ID 读取的路径保留实际类型标签，不只保留内部哈希槽位。"""
        pack = self.python("unsafe")
        candidate = self.verified(pack, "PY-SINK-SHELL")[0]
        restored = test_instance_fields.SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        catalog = test_instance_fields.SecurityEvidenceCatalog(restored.model_dump())
        sequence = catalog.selector.sequence(candidate.candidate_id)
        self.assertEqual({"app.py::Unsafe"}, {step["object_type_symbol"] for step in sequence if step.get("object_type_symbol")})

    def test_same_return_variable_preserves_type_alternatives(self) -> None:
        """A/B 分支先赋给同一个变量再 return，也不能合并两个类型的字段值。"""
        factory = " if flag:\n  result=Unsafe(value)\n else:\n  result=Safe(value)\n return result"
        for sink, expected in (("unsafe", True), ("safe", False)):
            with self.subTest(sink=sink):
                self.assertEqual(expected, bool(self.verified(self.python(sink, factory=factory), "PY-SINK-SHELL")))

    def test_java_and_typescript_virtual_targets(self) -> None:
        """相同协议消费 Java/TS 已解析的虚调用，不另外实现语言传播器。"""
        cases = (
            ("Main.java", "JAVA-SINK-PROCESS", 'abstract class Base {abstract void run();}class Unsafe extends Base {String command;Unsafe(String value){this.command=value;}void run(){UNSAFE}}class Safe extends Base {String command;Safe(String value){this.command="fixed";}void run(){SAFE}}class Main {static Base make(String value){if(flag){return new Unsafe(value);}return new Safe(value);}static void route(){String value=System.getenv("CMD");Base worker=make(value);worker.run();}}', 'Runtime.getRuntime().exec(this.command);'),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Base {run(){}}class Unsafe extends Base {constructor(value:string){super();this.command=value;}run(){UNSAFE}}class Safe extends Base {constructor(value:string){super();this.command='fixed';}run(){SAFE}}function make(value:string):Base {if(flag){return new Unsafe(value);}return new Safe(value);}function route(){const value=process.env.CMD;const worker:Base=make(value);worker.run();}", "exec(this.command);"),
        )
        for name, rule, source, operation in cases:
            for sink, expected in (("UNSAFE", True), ("SAFE", False)):
                with self.subTest(name=name, sink=sink):
                    pack = self.analyze({name: source.replace(sink, operation).replace("UNSAFE", "").replace("SAFE", "")})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_javascript_direct_fields_preserve_variants(self) -> None:
        """无类型注解的 JS 仍可返回多类实例并读取字段；方法派发仍要求既有解析依据。"""
        template = "import {exec} from 'node:child_process';class A {constructor(value){this.command=value;this.safe='fixed';}}class B {constructor(value){this.command='fixed';this.safe='fixed';}}function make(value){if(flag){return new A(value);}return new B(value);}function route(){const value=process.env.CMD;const worker=make(value);exec(worker.SELECTED);}"
        for field, expected in (("command", True), ("safe", False)):
            with self.subTest(field=field):
                pack = self.analyze({"app.js": template.replace("SELECTED", field)})
                self.assertEqual(expected, bool(self.verified(pack, "JS-SINK-SHELL")))
