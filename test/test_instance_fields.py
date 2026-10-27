"""公共实例字段协议与多语言构造器链路回归；只解析，不执行样例。"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphService
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog
from backend.app.services.security_analysis.contracts import SecurityEvidencePack


class InstanceFieldTests(unittest.TestCase):
    """相同公共算法必须保持实例、字段、覆写和真实证据位置的隔离。"""

    def analyze(self, files: dict[str, str]) -> SecurityEvidencePack:
        """对临时源码调用生产依赖图和安全分析入口，返回独立证据包。"""
        with tempfile.TemporaryDirectory() as directory:
            for name, source in files.items():
                Path(directory, name).write_text(source, encoding="utf8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            return SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"],
                                                      semantic_index=analysis["semantic_index"])

    def verified(self, pack: SecurityEvidencePack, rule: str) -> list:
        """只选实际建立值流的目标类别，结构候选不能充当字段传播。"""
        self.assertFalse(any(item.get("reason") == "program_graph_dataflow_error" for item in pack.scan_failures))
        return [item for item in pack.candidates if item.rule_id == rule and item.dataflow_verified]

    def test_python_constructor_field_and_readonly_method(self) -> None:
        """形参进入实例字段再被方法读取，保留构造器源码位置与原 instantiates 边。"""
        pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, command):\n  self.command=command\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value)\n worker.run()\n"})
        candidates = self.verified(pack, "PY-SINK-SHELL")
        self.assertTrue(candidates)
        flow = pack.dataflows[candidates[0].dataflow_id]
        self.assertTrue(any(step.symbol == "app.py::Worker::__init__" and step.location.line == 4 for step in flow.steps))
        allocations = {step.object_allocation_id for step in flow.steps if step.object_allocation_id}
        self.assertEqual(1, len(allocations))
        self.assertTrue(any(pack.call_edges[key].relation == "instantiates" for key in flow.call_edge_ids))
        restored = SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        catalog = SecurityEvidenceCatalog(restored.model_dump())
        steps, offset = [], 0
        while True:
            page = catalog.detail(candidates[0].candidate_id, section="flow", offset=offset, limit=10)
            steps.extend(page["items"])
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        self.assertEqual([step.model_dump() for step in flow.steps], steps)
        sequence = catalog.selector.sequence(candidates[0].candidate_id)
        self.assertEqual(allocations, {step["object_allocation_id"] for step in sequence if step.get("object_allocation_id")})
        self.assertTrue(json.loads(catalog.summary(max_chars=20000))["items"])

    def test_other_instance_safe_field_and_overwrites_stay_separate(self) -> None:
        """同类不等于同一实例；构造器与调用方的确定覆盖不能保留旧污点。"""
        for setup, field, initial, expected in (
            ("first=Worker(value)\n second=Worker('fixed')\n second.run()", "command", "", False),
            ("first=Worker(value)\n first.run()", "safe", "", False),
            ("first=Worker(value)\n first.command='fixed'\n first.run()", "command", "", False),
            ("first=Worker(value)\n first=Worker('fixed')\n first.run()", "command", "", False),
            ("first=Worker(value)\n first.run()", "command", "\n  self.command='fixed'", False),
            ("first=Worker(value)\n first.run()", "command", "", True),
        ):
            with self.subTest(setup=setup, field=field, initial=initial):
                source = "import os\nclass Worker:\n def __init__(self, command):\n  self.command=command\n  self.safe='fixed'" + initial + "\n def run(self):\n  os.system(self." + field + ")\ndef route():\n value=input()\n " + setup + "\n"
                self.assertEqual(expected, bool(self.verified(self.analyze({"app.py": source}), "PY-SINK-SHELL")))

    def test_java_javascript_and_typescript_use_same_field_algorithm(self) -> None:
        """各语言只规范实例语法，不各自实现构造器污点引擎。"""
        cases = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker { String command; String safe; Worker(String value){this.command=value;this.safe="fixed";} void run(){Runtime.getRuntime().exec(this.SELECTED);} static void route(){String value=System.getenv("CMD");Worker worker=new Worker(value);worker.run();}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process'; class Worker {constructor(value){this.command=value;this.safe='fixed';}run(){exec(this.SELECTED);}} function route(){const value=process.env.CMD;const worker=new Worker(value);worker.run();}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process'; class Worker {constructor(value:string){this.command=value;this.safe='fixed';}run(){exec(this.SELECTED);}} function route(){const value=process.env.CMD;const worker=new Worker(value);worker.run();}"),
        )
        for name, rule, source in cases:
            for selected, expected in (("command", True), ("safe", False)):
                with self.subTest(name=name, selected=selected):
                    self.assertEqual(expected, bool(self.verified(self.analyze({name: source.replace("SELECTED", selected)}), rule)))

    def test_instance_context_is_persistent_and_old_graphs_are_readable(self) -> None:
        """实例上下文是公共语法事实，旧程序图可缺省，原始程序图不包含私有字段摘要。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text("class Worker:\n def __init__(me, value):\n  me.value=value\n def read(me):\n  return me.value\n", encoding="utf8")
            graph = ProgramGraphService().analyze(directory)
        restored = ProgramGraphArtifact.model_validate_json(graph.model_dump_json())
        contexts = [function.instance_context for function in restored.functions.values()]
        self.assertTrue(all(context and context.receiver == "me" for context in contexts))
        self.assertEqual(1, sum(context.is_constructor for context in contexts))
        self.assertTrue(all(not function.field_effects for function in graph.functions.values()))
        self.assertEqual({}, ProgramGraphArtifact.model_validate({"schema_version": "1.2"}).functions)

    def test_cross_file_constructor_and_field_return(self) -> None:
        """构造器、读取方法及返回结果跨文件连接，不把字段当成整个返回对象。"""
        for returned, expected in (("self.command", True), ("'fixed'", False)):
            with self.subTest(returned=returned):
                pack = self.analyze({"worker.py": "class Worker:\n def __init__(self, command):\n  self.command=command\n def read(self):\n  return " + returned + "\n",
                                     "app.py": "import os\nfrom worker import Worker\ndef route():\n value=input()\n worker=Worker(command=value)\n command=worker.read()\n os.system(command)\n"})
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")))

    def test_constructor_normal_exit_and_branches(self) -> None:
        """以成功构造的出口值计算摘要；抛异常与确定覆盖不冒充有效实例字段。"""
        for ending, expected in (("\n  raise ValueError('failed')", False),
                                  ("\n  if flag:\n   self.command='fixed'", True),
                                  ("\n  if flag:\n   self.command='fixed'\n  else:\n   self.command='safe'", False)):
            with self.subTest(ending=ending):
                pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, command, flag):\n  self.command=command" + ending + "\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value, flag)\n worker.run()\n"})
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")))

    def test_arbitrary_receiver_name(self) -> None:
        """Python 首接收者不是靠 self 字符串猜测。"""
        pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(me, command):\n  me.command=command\n def run(me):\n  os.system(me.command)\ndef route():\n value=input()\n worker=Worker(value)\n worker.run()\n"})
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))

    def test_constructor_body_arguments_across_languages(self) -> None:
        """同一公共边界进入初始化方法，危险参数与安全兄弟参数不混淆。"""
        cases = (
            ("app.py", "PY-SINK-SHELL", "import os\nclass Worker:\n def __init__(me, value, safe):\n  os.system(SELECTED)\ndef route():\n value=input()\n Worker(value, 'fixed')\n"),
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker {Worker(String value,String safe){Runtime.getRuntime().exec(SELECTED);}static void route(){String value=System.getenv("CMD");new Worker(value,"fixed");}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker {constructor(value,safe){exec(SELECTED);}}function route(){const value=process.env.CMD;new Worker(value,'fixed');}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker {constructor(value:string,safe:string){exec(SELECTED);}}function route(){const value=process.env.CMD;new Worker(value,'fixed');}"),
        )
        for name, rule, source in cases:
            for selected, expected in (("value", True), ("safe", False)):
                with self.subTest(name=name, selected=selected):
                    pack = self.analyze({name: source.replace("SELECTED", selected)})
                    candidates = self.verified(pack, rule)
                    self.assertEqual(expected, bool(candidates))
                    if candidates:
                        flow = pack.dataflows[candidates[0].dataflow_id]
                        self.assertTrue(flow.value_boundary_ids)
                        self.assertTrue(any(pack.call_edges[key].relation == "instantiates" for key in flow.call_edge_ids))
                        self.assertTrue(any(step.object_allocation_id for step in flow.steps))

    def test_nested_and_factory_constructor_body_without_object_return_taint(self) -> None:
        """复杂分配仍能核验初始化体，但不把初始化 return 污点映射为整个对象。"""
        for creation in ("Worker(input())", "make(input())"):
            with self.subTest(creation=creation):
                pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, value):\n  os.system(value)\n  return value\ndef make(value):\n return Worker(value)\ndef route():\n worker=" + creation + "\n os.system(worker)\n"})
                candidates = self.verified(pack, "PY-SINK-SHELL")
                self.assertTrue(candidates)
                self.assertEqual({4}, {pack.facts[item.sink_fact_id].location.line for item in candidates})

    def test_factory_return_supported_but_nested_input_is_not_guessed(self) -> None:
        """工厂返回已经支持；带嵌套 Source 的复杂创建仍保留显式缺口。"""
        for creation, expected in (("make(value)", True), ("Worker(input())", False)):
            with self.subTest(creation=creation):
                pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, command):\n  self.command=command\n def run(self):\n  os.system(self.command)\ndef make(value):\n return Worker(value)\ndef route():\n value=input()\n worker=" + creation + "\n worker.run()\n"})
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")))
                if not expected:
                    self.assertTrue(pack.flow_diagnostics["counts"].get("instance_allocation_not_modelled", 0))

    def test_java_implicit_fields_shadowing_and_static_storage(self) -> None:
        """隐式实例字段规范为 this；参数遮蔽、static 存储不能冒充同一实例字段。"""
        for body, expected in (("Runtime.getRuntime().exec(command);", True),
                                ('String command="fixed";Runtime.getRuntime().exec(command);', False)):
            with self.subTest(body=body):
                source = 'class Worker {String command;Worker(String value){command=value;}void run(){' + body + '}static void route(){String value=System.getenv("CMD");Worker worker=new Worker(value);worker.run();}}'
                self.assertEqual(expected, bool(self.verified(self.analyze({"Main.java": source}), "JAVA-SINK-PROCESS")))
        source = 'class Worker {static String command;Worker(String value){this.command=value;}void run(){Runtime.getRuntime().exec(this.command);}static void route(){String value=System.getenv("CMD");Worker worker=new Worker(value);worker.run();}}'
        pack = self.analyze({"Main.java": source})
        self.assertFalse(self.verified(pack, "JAVA-SINK-PROCESS"))
        self.assertTrue(pack.flow_diagnostics["counts"].get("instance_shared_storage_not_modelled"))

    def test_java_implicit_field_source_in_same_method_still_maps(self) -> None:
        """字段规范化不能使既有本地 Source 与程序图身份脱节。"""
        pack = self.analyze({"Main.java": 'class Main {String command;void run(){command=System.getenv("CMD");Runtime.getRuntime().exec(command);}}'})
        self.assertTrue(self.verified(pack, "JAVA-SINK-PROCESS"))

    def test_rebound_constructor_receiver_is_not_original_instance(self) -> None:
        """重绑定首参数后写出的字段属于别的对象，不套用构造器实例摘要。"""
        pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, command):\n  self=other\n  self.command=command\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value)\n worker.run()\n"})
        self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
        self.assertTrue(pack.flow_diagnostics["counts"].get("instance_receiver_rebound"))

    def test_escape_or_mutation_is_an_explicit_frontier(self) -> None:
        """未知对象操作不沿用旧字段摘要，也不能退回“整个对象受污”的快捷路径。"""
        for operation in ("unknown(worker)", "worker.unknown_method()"):
            for selected in ("worker.run()", "os.system(worker.safe)"):
                with self.subTest(operation=operation, selected=selected):
                    pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, value):\n  self.command=value\n  self.safe='fixed'\n def reset(self):\n  self.command='fixed'\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value)\n " + operation + "\n " + selected + "\n"})
                    self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                    self.assertTrue(pack.flow_diagnostics["counts"].get("instance_alias_escape_or_mutation_not_modelled"))

    def test_java_abstract_and_interface_methods_have_no_body(self) -> None:
        """合法的无方法体声明不能使新语法适配中断整个程序图构建。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Types.java").write_text("interface Handler { void run(); } abstract class Worker { String value; abstract void read(); }", encoding="utf8")
            graph = ProgramGraphService().analyze(directory)
        self.assertEqual(1, graph.coverage.files_parsed)
        self.assertFalse(graph.failures)
        self.assertEqual(2, len(graph.functions))

    def test_field_evidence_redacts_embedded_credentials(self) -> None:
        """新摘要的源码溯源使用统一遮蔽，不额外泄漏片段已隐藏的凭据。"""
        secret = "sk-abcdefghijklmnop123456"
        pack = self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self, command):\n  self.command=command+'" + secret + "'\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value)\n worker.run()\n"})
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
        self.assertNotIn(secret, pack.model_dump_json())

    def test_object_overlay_does_not_mutate_original_program_graph(self) -> None:
        """字段接口、分配和摘要只写入私有视图，公共程序图可继续被其他消费者复用。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text("import os\nclass Worker:\n def __init__(self,value):\n  self.command=value\n def run(self):\n  os.system(self.command)\ndef route():\n value=input()\n worker=Worker(value)\n worker.run()\n", encoding="utf8")
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1).run_full_analysis()
            graph = ProgramGraphService().analyze(directory, dependency_graph=analysis["dependency_graph"])
            before = graph.model_dump_json()
            provider = Mock()
            provider.analyze.return_value = graph
            pack = SecurityAnalysisService(program_graph_service=provider).analyze(project_root=directory,
                dependency_graph=analysis["dependency_graph"], semantic_index=analysis["semantic_index"])
            self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
            self.assertEqual(before, graph.model_dump_json())
