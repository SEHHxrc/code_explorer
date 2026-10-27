"""公共工厂返回链路回归：只解析临时源码，不执行目标或调用模型。"""

import unittest

from test import test_instance_fields


class FactoryReturnTests(unittest.TestCase):
    """所有语言共用字段返回接口；覆盖安全反例和调用上下文隔离。"""

    analyze = test_instance_fields.InstanceFieldTests.analyze
    verified = test_instance_fields.InstanceFieldTests.verified

    def python(self, factory: str, route: str = "worker=make(value)\n worker.run()", selected: str = "command") -> test_instance_fields.SecurityEvidencePack:
        """用相同 Source/实例/Sink 模板，仅改变工厂实现或调用形式。"""
        return self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self,value):\n  self.command=value\n  self.safe='fixed'\n def run(self):\n  os.system(self." + selected + ")\n" + factory + "\ndef route():\n value=input()\n " + route + "\n"})

    def test_direct_local_and_forwarded_factories(self) -> None:
        """直接创建、返回局部变量及多层转发复用同一返回机制。"""
        for factory in ("def make(value):\n return Worker(value)",
                        "def make(value):\n worker=Worker(value)\n return worker",
                        "def inner(value):\n return Worker(value)\ndef make(value):\n return inner(value)"):
            with self.subTest(factory=factory):
                pack = self.python(factory)
                candidates = self.verified(pack, "PY-SINK-SHELL")
                self.assertTrue(candidates)
                flow = pack.dataflows[candidates[0].dataflow_id]
                self.assertTrue(flow.value_boundary_ids)
                self.assertTrue(any("worker.command" in step.output_names and step.object_allocation_id for step in flow.steps))

    def test_factory_context_fields_and_overwrites(self) -> None:
        """同一工厂的两个调用不可串联；安全字段和确定覆盖不能保留污点。"""
        for route, selected in (("first=make(value)\n worker=make('fixed')\n worker.run()", "command"),
                                ("worker=make(value)\n worker.run()", "safe"),
                                ("worker=make(value)\n worker.command='fixed'\n worker.run()", "command"),
                                ("worker=make(value)\n worker=make('fixed')\n worker.run()", "command")):
            with self.subTest(route=route, selected=selected):
                self.assertFalse(self.verified(self.python("def make(value):\n return Worker(value)", route, selected), "PY-SINK-SHELL"))

    def test_factory_source_and_safe_replacement(self) -> None:
        """工厂内部产生的 Source 能返回字段，参数在构造前被安全替换则不传播。"""
        pack = self.python("def make(unused):\n value=input()\n return Worker(value)")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
        pack = self.python("def make(value):\n value='fixed'\n return Worker(value)")
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))

    def test_returned_instance_field_overwrite_and_other_instance(self) -> None:
        """返回前覆盖和返回另一个安全实例，都不能继承未返回实例的污点。"""
        for factory in ("def make(value):\n worker=Worker(value)\n worker.command='fixed'\n return worker",
                        "def make(value):\n first=Worker(value)\n worker=Worker('fixed')\n return worker"):
            with self.subTest(factory=factory):
                self.assertFalse(self.verified(self.python(factory), "PY-SINK-SHELL"))

    def test_conditional_returns_same_type_and_static_method(self) -> None:
        """同一具体类型的分支返回按 may 合并；Python 静态工厂不要求实例接收者。"""
        pack = self.python("def make(value):\n if flag:\n  return Worker(value)\n return Worker('fixed')")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
        pack = self.python("def make(value):\n if first:\n  return Worker('fixed')\n elif second:\n  return Worker(value)\n else:\n  return Worker('fixed')")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
        pack = self.python("class Factory:\n @staticmethod\n def make(value):\n  return Worker(value)",
                           "worker=Factory.make(value)\n worker.run()")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))

    def test_cross_file_and_direct_field_read(self) -> None:
        """跨文件工厂与字段直接读取共用原始调用身份，不凭同名函数补边。"""
        pack = self.analyze({"worker.py": "class Worker:\n def __init__(self,value):\n  self.command=value\ndef make(value):\n return Worker(value)\n",
                             "app.py": "import os\nfrom worker import make\ndef route():\n value=input()\n worker=make(value)\n os.system(worker.command)\n"})
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))

    def test_unresolved_polymorphic_dispatch_is_not_guessed(self) -> None:
        """无类型依据且同名方法有歧义时，不在安全层凭方法名新增调用边。"""
        pack = self.python("class Other:\n def __init__(self,value):\n  self.command='fixed'\n def run(self):\n  os.system(self.command)\ndef make(value):\n if flag:\n  return Worker(value)\n return Other(value)")
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(pack.flow_diagnostics["counts"].get("instance_factory_receiver_not_modelled", 0))
        pack = self.python("class Other:\n def __init__(self,value):\n  self.command='fixed'\n def run(self):\n  os.system(self.command)\ndef make(value):\n if first:\n  return Worker(value)\n elif second:\n  return Other(value)\n else:\n  return Worker(value)")
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(pack.flow_diagnostics["counts"].get("instance_factory_receiver_not_modelled", 0))

    def test_factory_depth_is_bounded_and_diagnosed(self) -> None:
        """超过四层的返回组合不能无限展开，也不能静默声称已覆盖。"""
        factory = "def first(value):\n return Worker(value)\n"
        previous = "first"
        for current in ("second", "third", "fourth", "fifth", "make"):
            factory += "def " + current + "(value):\n return " + previous + "(value)\n"
            previous = current
        pack = self.python(factory)
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(pack.flow_diagnostics["counts"].get("instance_factory_depth_limit", 0))

    def test_factory_does_not_taint_whole_returned_object(self) -> None:
        """字段值不能因为工厂转发而升级为整个对象的字符串内容。"""
        pack = self.python("def make(value):\n return Worker(value)", "worker=make(value)\n os.system(worker)")
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))

    def test_async_and_generator_results_are_not_plain_instances(self) -> None:
        """仅识别延迟返回边界，不把协程/Promise/迭代器当已完成的实例，也不分析并发。"""
        for factory in ("async def make(value):\n return Worker(value)",
                        "def make(value):\n yield 1\n return Worker(value)"):
            with self.subTest(factory=factory):
                pack = self.python(factory)
                self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                self.assertTrue(any(item.get("reason_detail") == "deferred_return" for item in pack.flow_diagnostics["items"]))
        for expression in ("[worker]", "{item:worker}"):
            with self.subTest(expression=expression):
                source = "import {exec} from 'node:child_process';class Worker {constructor(value){this.command=value;}run(){exec(this.command);}}function make(value){const worker=new Worker(value);return " + expression + ";}function route(){const value=process.env.CMD;const worker=make(value);worker.run();}"
                self.assertFalse(self.verified(self.analyze({"app.js": source}), "JS-SINK-SHELL"))
        for declaration in ("async function make(value)", "function* make(value)"):
            with self.subTest(declaration=declaration):
                source = "import {exec} from 'node:child_process';class Worker {constructor(value){this.command=value;}run(){exec(this.command);}}" + declaration + "{return new Worker(value);}function route(){const value=process.env.CMD;const worker=make(value);worker.run();}"
                self.assertFalse(self.verified(self.analyze({"app.js": source}), "JS-SINK-SHELL"))

    def test_factory_flow_delivery_retains_original_calls_and_returns(self) -> None:
        """结构化详情及导航序列保留初始化、真实 return 和原始调用边。"""
        pack = self.python("def make(value):\n return Worker(value)")
        candidate = self.verified(pack, "PY-SINK-SHELL")[0]
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(any(step.symbol == "app.py::make" and step.location.line == 9 for step in flow.steps))
        self.assertTrue({"calls", "instantiates"}.issubset({pack.call_edges[key].relation for key in flow.call_edge_ids}))
        restored = test_instance_fields.SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        catalog = test_instance_fields.SecurityEvidenceCatalog(restored.model_dump())
        delivered, offset = [], 0
        while True:
            page = catalog.detail(candidate.candidate_id, section="flow", offset=offset, limit=10)
            delivered.extend(page["items"])
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        self.assertEqual([step.model_dump() for step in flow.steps], delivered)
        self.assertTrue(any(step.get("object_allocation_id") for step in catalog.selector.sequence(candidate.candidate_id)))

    def test_java_javascript_typescript_factory(self) -> None:
        """Java 静态工厂与 JS/TS 普通工厂不实现独立的返回引擎。"""
        cases = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker {String command;Worker(String value){this.command=value;}void run(){Runtime.getRuntime().exec(this.command);}static Worker make(String value){return new Worker(value);}static void route(){String value=System.getenv("CMD");Worker worker=make(value);worker.run();}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker {constructor(value){this.command=value;}run(){exec(this.command);}}function make(value){return new Worker(value);}function route(){const value=process.env.CMD;const worker=make(value);worker.run();}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker {constructor(value:string){this.command=value;}run(){exec(this.command);}}function make(value:string){return new Worker(value);}function route(){const value=process.env.CMD;const worker=make(value);worker.run();}"),
        )
        for name, rule, source in cases:
            with self.subTest(name=name):
                self.assertTrue(self.verified(self.analyze({name: source}), rule))

    def test_unknown_return_mutation_alias_and_types_are_frontiers(self) -> None:
        """未知返回、类型歧义和副作用不通过工厂返回绕过原有实例限制。"""
        factories = (
            "def make(value):\n worker=Worker(value)\n worker=other\n return worker",
            "def make(value):\n worker=Worker(value)\n unknown(worker)\n return worker",
            "def make(value):\n worker=Worker(value)\n return [worker]",
            "def make(value):\n worker=Worker(value)\n return {'item':worker}",
            "def make(value):\n if flag:\n  return Worker(value)\n return other",
        )
        for factory in factories:
            with self.subTest(factory=factory):
                self.assertFalse(self.verified(self.python(factory), "PY-SINK-SHELL"))
