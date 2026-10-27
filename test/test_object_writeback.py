"""公共对象调用值跟踪：显式参数与接收者共享字段入口/正常出口/调用后写回。"""

import unittest

from test import test_instance_fields as fixtures


class ObjectWritebackTests(unittest.TestCase):
    """端到端正反例，不运行目标源码、不调用真实模型。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def python(self, body: str, helper: str = "", setter: str = "self.command=value") -> fixtures.SecurityEvidencePack:
        """用临时 Python 项目运行真实依赖/安全服务，返回完整证据包。"""
        source = '''import os
class Worker:
 def __init__(self,value):
  self.command=value
  self.safe='fixed'
 def set(self,value):
  SETTER
 def reset(self):
  self.command='fixed'
 def run(self):
  os.system(self.command)
HELPER
def route():
 value=input()
BODY
'''.replace("SETTER", setter).replace("HELPER", helper).replace("BODY", "".join(" " + line + "\n" for line in body.splitlines()))
        return self.analyze({"app.py": source})

    def test_receiver_and_explicit_parameter_share_writeback(self) -> None:
        """普通标量实参经真实字段写入回到同一实例，方法和普通函数使用相同正常出口边界。"""
        for operation, helper in (("worker.set(value)", ""),
                                  ("change(worker,value)", "def change(obj,value):\n obj.command=value"),
                                  ("change(value=value,obj=worker)", "def change(obj,value):\n obj.command=value")):
            with self.subTest(operation=operation):
                pack = self.python("worker=Worker('fixed')\n" + operation + "\nworker.run()", helper)
                candidates = self.verified(pack, "PY-SINK-SHELL")
                self.assertTrue(candidates, pack.flow_diagnostics)
                steps = pack.dataflows[candidates[0].dataflow_id].steps
                self.assertTrue(any(step.callsite_id and step.object_allocation_id for step in steps))

    def test_definite_clean_write_and_conditional_write_are_not_equivalent(self) -> None:
        """确定覆盖杀死旧字段，条件写入保留未执行分支；兄弟字段和其他实例不能污染。"""
        for body, setter, expected in (
            ("worker=Worker(value)\nworker.reset()\nworker.run()", "self.command=value", False),
            ("worker=Worker(value)\nworker.set('fixed')\nworker.run()", "if flag:\n   self.command=value", True),
            ("worker=Worker(value)\nworker.set('fixed')\nworker.run()", "if flag:\n   self.command=value\n  else:\n   self.command='safe'", False),
            ("worker=Worker('fixed')\nworker.set(value)\nworker.run()", "self.safe=value", False),
            ("worker=Worker('fixed')\nother=Worker('fixed')\nother.set(value)\nworker.run()", "self.command=value", False),
            ("worker=Worker('fixed')\nalias=worker\nalias.set(value)\nworker.run()", "self.command=value", True),
            ("worker=Worker(value)\nalias=worker\nalias.reset()\nworker.run()", "self.command=value", False),
        ):
            with self.subTest(body=body, setter=setter):
                pack = self.python(body, setter=setter)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_sources_inside_callee_and_object_field_inputs(self) -> None:
        """被调者直接产生 Source 和被调者读取对象字段都走原公共传播器，不把整个对象标污。"""
        for body, helper, setter, expected in (
            ("worker=Worker('fixed')\nworker.set('fixed')\nworker.run()", "", "self.command=input()", True),
            ("worker=Worker('fixed')\nchange(worker)\nworker.run()", "def change(obj):\n obj.command=input()", "self.command=value", True),
            ("worker=Worker(value)\nconsume(worker)", "def consume(obj):\n os.system(obj.command)", "self.command=value", True),
            ("worker=Worker(value)\nconsume(worker)", "def consume(obj):\n os.system(obj.safe)", "self.command=value", False),
        ):
            with self.subTest(body=body, helper=helper):
                pack = self.python(body, helper, setter)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_rebinding_unknown_effect_alias_conflict_and_exception_do_not_guess(self) -> None:
        """形参局部重绑定、对象逃逸、同对象占两个形参和无正常出口不冒充精确字段写回。"""
        for operation, helper in (
            ("change(worker,value)", "def change(obj,value):\n obj=other\n obj.command=value"),
            ("change(worker,value)", "def change(obj,value):\n unknown(obj)\n obj.command=value"),
            ("change(worker,worker,value)", "def change(a,b,value):\n a.command=value\n b.command='fixed'"),
            ("change(worker,value)", "def change(obj,value):\n obj.command=value\n raise ValueError('failed')"),
            ("change(worker+'',value)", "def change(obj,value):\n obj.command=value"),
            ("change(worker,worker+'',value)", "def change(a,b,value):\n a.command=value\n b.command='fixed'"),
            ("change(worker,value)", "def change(obj,value):\n def inner():\n  obj.command=value\n inner()"),
        ):
            with self.subTest(helper=helper):
                pack = self.python("worker=Worker('fixed')\n" + operation + "\nworker.run()", helper)
                self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                self.assertTrue(pack.flow_diagnostics["counts"].get("instance_alias_escape_or_mutation_not_modelled"))

    def test_call_order_constant_outputs_and_abrupt_exits(self) -> None:
        """写回不越过调用时序、不串调用上下文，抛出路径不进入正常字段出口。"""
        for body, setter, expected in (
            ("worker=Worker('fixed')\nworker.run()\nworker.set(value)", "self.command=value", False),
            ("worker=Worker('fixed')\nworker.set(value)\nworker.set('fixed')\nworker.run()", "self.command=value", False),
            ("worker=Worker('fixed')\nworker.set('fixed')\nworker.set(value)\nworker.run()", "self.command=value", True),
            ("worker=Worker(value)\nworker.set('fixed')\nworker.run()", "self.command=self.safe", False),
            ("worker=Worker(value)\nworker.set('fixed')\nworker.run()", "if flag:\n   self.command=value\n   raise ValueError('failed')\n  self.command='fixed'", False),
            ("worker=Worker('fixed')\nworker.set(value)\nworker.run()", "self.command+=value", True),
        ):
            with self.subTest(body=body, setter=setter):
                pack = self.python(body, setter=setter)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_java_javascript_typescript_use_same_argument_and_receiver_effects(self) -> None:
        """语言只提供已有语法/调用事实，参数字段写回与 this 修改不各自实现求解器。"""
        cases = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker{String command;Worker(String v){this.command=v;}void set(String v){this.command=v;}void reset(){this.command="fixed";}void run(){Runtime.getRuntime().exec(this.command);}static void change(Worker obj,String v){obj.command=v;}static void route(){String value=System.getenv("CMD");Worker worker=new Worker(INITIAL);OPERATION worker.run();}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v){this.command=v;}set(v){this.command=v;}reset(){this.command='fixed';}run(){exec(this.command);}}function change(obj,v){obj.command=v;}function route(){const value=process.env.CMD;const worker=new Worker(INITIAL);OPERATION worker.run();}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v:string){this.command=v;}set(v:string){this.command=v;}reset(){this.command='fixed';}run(){exec(this.command);}}function change(obj:Worker,v:string){obj.command=v;}function route(){const value=process.env.CMD;const worker=new Worker(INITIAL);OPERATION worker.run();}"),
        )
        for path, rule, template in cases:
            for initial, operation, expected in (
                ('"fixed"', "worker.set(value);", True),
                ('"fixed"', "change(worker,value);", True),
                ("value", "worker.reset();", False),
                ("value", 'change(worker,"fixed");', False),
            ):
                with self.subTest(path=path, operation=operation):
                    source = template.replace("INITIAL", initial).replace("OPERATION", operation)
                    pack = self.analyze({path: source})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_cross_file_object_parameter_and_multiple_distinct_objects(self) -> None:
        """对象参数跨文件仍引用既有调用边；两个不同实例的写入不串值。"""
        for selected, expected in (("first", True), ("second", False)):
            with self.subTest(selected=selected):
                pack = self.analyze({
                    "worker.py": "class Worker:\n def __init__(self,v):\n  self.command=v\n",
                    "helper.py": "def change(first,second,value):\n first.command=value\n second.command='fixed'\n",
                    "app.py": "import os\nfrom worker import Worker\nfrom helper import change\ndef route():\n value=input()\n first=Worker('fixed')\n second=Worker('fixed')\n change(first,second,value)\n os.system(" + selected + ".command)\n",
                })
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_known_factory_result_reuses_same_call_effect_interface(self) -> None:
        """已识别单类型工厂结果不另建写回算法，别名、参数与接收者共用同一接口。"""
        helper = "def make(value):\n return Worker(value)\ndef change(obj,value):\n obj.command=value"
        for body, expected in (
            ("worker=make('fixed')\nworker.set(value)\nworker.run()", True),
            ("worker=make('fixed')\nchange(worker,value)\nworker.run()", True),
            ("worker=make(value)\nworker.reset()\nworker.run()", False),
            ("worker=make(value)\nalias=worker\nalias.reset()\nworker.run()", False),
        ):
            with self.subTest(body=body):
                pack = self.python(body, helper)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)


if __name__ == "__main__":
    unittest.main()
