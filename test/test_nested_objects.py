"""公共嵌套对象引用的端到端验证；不执行目标程序，不请求模型。"""

import unittest
from unittest.mock import patch

from test import test_instance_fields as fixtures
from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphEdge, ProgramGraphNode, ProgramValueTransfer
from backend.app.services.security_analysis.flow_analysis.object_references import ObjectReferenceIndex
from backend.app.services.security_analysis.flow_analysis.object_contracts import LocalInstance
from backend.app.services.security_analysis.flow_analysis.diagnostics import FlowDiagnostics
from backend.app.services.security_analysis.flow_analysis.objects import receiver_members
from backend.app.services.value_binding.objects import storage_slot


class NestedObjectTests(unittest.TestCase):
    """组合字段逐跳定位到同一子对象，而不是将整条访问路径扁平化为父对象字段。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def test_nested_constructor_accesses_never_become_direct_snapshot_fields(self):
        """工厂快照共用直属字段契约；嵌套路径/下标不能作为一个字段送入 Pydantic。"""
        members = receiver_members({'self.child.value', 'self.child.values[0]', 'self.items["key"].value', 'self.safe'})
        self.assertEqual({'child','items','safe'},members)
        self.assertEqual(3,len({storage_slot('allocation', member).value_id for member in members}))

    def python(self, body: str, helper: str = "", constructor: str = "self.child=child") -> fixtures.SecurityEvidencePack:
        """输入局部操作，返回真实生产分析器生成的静态证据包。"""
        return self.analyze({"app.py": '''import os
class Worker:
 def __init__(self, value):
  self.command=value
  self.safe='fixed'
 def run(self):
  os.system(self.command)
 def set(self, value):
  self.command=value
class Holder:
 def __init__(self, child):
  self.child=child
 def run(self):
  os.system(self.child.command)
 def set(self, value):
  self.child.command=value
HELPER
def route():
 value=input()
'''.replace("HELPER", helper).replace("self.child=child", constructor)
            + "".join(" " + line + "\n" for line in body.splitlines())})

    def test_nested_constructor_and_local_field_assignment(self) -> None:
        """构造器保存引用、普通字段赋值及多层组合都到达真正子对象的末级字段。"""
        for body, expected in (
            ("child=Worker(value)\nparent=Holder(child)\nos.system(parent.child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nos.system(parent.child.safe)", False),
            ("child=Worker(value)\nparent=Holder(None)\nparent.child=child\nos.system(parent.child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nroot=Holder(parent)\nos.system(root.child.child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nalias=parent.child\nos.system(alias.command)", True),
            ("child=Worker('fixed')\nparent=Holder(child)\nparent.child.command=value\nos.system(child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nparent.child.command='fixed'\nos.system(child.command)", False),
            ("parent=Holder(None)\nparent.child=Worker(value)\nos.system(parent.child.command)", True),
            ("parent=Holder(None)\nparent.child=Worker('fixed')\nparent.child.command=value\nos.system(parent.child.command)", True),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_unknown_escape_or_transform_does_not_reuse_old_child_summary(self) -> None:
        """父/子任一端未知逃逸或变换引用都不能保留旧的确定字段绑定，且必须暴露分析边界。"""
        for operation in ("unknown(parent)", "unknown(parent.child)", "parent.child.unknown_method()",
                          "external.child=child", "parent.child=str(child)"):
            with self.subTest(operation=operation):
                pack = self.python("child=Worker(value)\nparent=Holder(child)\n" + operation + "\nos.system(parent.child.command)")
                self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                self.assertTrue(pack.flow_diagnostics["counts"].get("instance_alias_escape_or_mutation_not_modelled")
                                or pack.flow_diagnostics["counts"].get("instance_nested_reference_not_resolved"), pack.flow_diagnostics)

    def test_constructor_reference_storage_requires_consistent_normal_exit(self) -> None:
        """引用摘要只接受正常出口一致的原对象；局部别名可用，条件替换、运算与未知副作用不可用。"""
        for constructor, expected in (
            ("alias=child\n  self.child=alias", True),
            ("self.child=child\n  self.child=None", False),
            ("if flag:\n   self.child=child\n  else:\n   self.child=None", False),
            ("if flag:\n   self.child=child\n  else:\n   self.child=child", True),
            ("self.child=str(child)", False),
            ("unknown(child)\n  self.child=child", False),
        ):
            with self.subTest(constructor=constructor):
                pack = self.python("child=Worker(value)\nparent=Holder(child)\nos.system(parent.child.command)", constructor=constructor)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_distinct_child_fields_do_not_share_state(self) -> None:
        """两个不同子对象的同名字段不能串流，写回和证据只能归属于实际对象。"""
        template = '''import os
class Child:
 def __init__(self,command):
  self.command=command
class Pair:
 def __init__(self,left,right):
  self.left=left
  self.right=right
def route():
 value=input()
 left=Child(value)
 right=Child('fixed')
 parent=Pair(left,right)
 MUTATION
 os.system(SELECTED)
'''
        for selected, mutation, expected in (
            ("parent.left.command", "pass", True),
            ("parent.right.command", "pass", False),
            ("parent.left.command", "parent.right.command='fixed'", True),
            ("parent.right.command", "parent.left.command=value", False),
            ("parent.right.command", "parent.right.command=value", True),
        ):
            with self.subTest(selected=selected, mutation=mutation):
                pack = self.analyze({"app.py": template.replace("SELECTED", selected).replace("MUTATION", mutation)})
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_nested_alias_steps_survive_evidence_delivery_without_mutating_graph(self) -> None:
        """模型按 ID 获取的路径包含组合赋值来源，原程序图仍不包含消费者的堆状态。"""
        captured = []
        original = fixtures.ProgramGraphService.analyze

        def observe(service: fixtures.ProgramGraphService, *args: object, **kwargs: object) -> fixtures.ProgramGraphArtifact:
            """保留生产者原始图及快照，仍执行真实服务。"""
            graph = original(service, *args, **kwargs)
            captured.append((graph, graph.model_dump_json()))
            return graph

        with patch.object(fixtures.ProgramGraphService, "analyze", autospec=True, side_effect=observe):
            pack = self.python("child=Worker(value)\nparent=Holder(None)\nparent.child=child\nos.system(parent.child.command)")
        self.assertTrue(all(graph.model_dump_json() == before for graph, before in captured))
        candidate = self.verified(pack, "PY-SINK-SHELL")[0]
        restored = fixtures.SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        steps = restored.dataflows[candidate.dataflow_id].steps
        self.assertTrue(any(step.expression == "parent.child=child" and step.object_allocation_id for step in steps))
        catalog = fixtures.SecurityEvidenceCatalog(restored.model_dump())
        page = catalog.detail(candidate.candidate_id, section="flow", offset=0, limit=100)
        self.assertEqual([step.model_dump() for step in steps], page["items"])
        pack = self.python("child=Worker(value)\nparent=Holder(child)\nos.system(parent.child.command)")
        candidate = self.verified(pack, "PY-SINK-SHELL")[0]
        self.assertTrue(any(step.expression == "self.child=child" and step.symbol.endswith("Holder::__init__")
                            for step in pack.dataflows[candidate.dataflow_id].steps))

    def test_nested_receiver_and_parameter_share_existing_call_effects(self) -> None:
        """嵌套字段读写和子对象实参共用原方法/形参边界，不新增调用目标或污点求解。"""
        for body, helper, expected in (
            ("child=Worker(value)\nparent=Holder(child)\nparent.run()", "", True),
            ("child=Worker('fixed')\nparent=Holder(child)\nparent.set(value)\nos.system(child.command)", "", True),
            ("child=Worker(value)\nparent=Holder(child)\nparent.set('fixed')\nparent.run()", "", False),
            ("child=Worker('fixed')\nparent=Holder(child)\nchange(parent.child,value)\nos.system(child.command)",
             "def change(obj,value):\n obj.command=value", True),
            ("child=Worker('fixed')\nparent=Holder(child)\nchange(parent,value)\nos.system(child.command)",
             "def change(obj,value):\n obj.child.command=value", True),
            ("child=Worker(value)\nparent=Holder(child)\nchange(parent,'fixed')\nos.system(child.command)",
             "def change(obj,value):\n obj.child.command=value", False),
            ("child=Worker(value)\nparent=Holder(child)\nconsume(parent)",
             "def consume(obj):\n os.system(obj.child.command)", True),
        ):
            with self.subTest(body=body):
                pack = self.python(body, helper)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)


    def test_nested_fields_across_reference_languages(self) -> None:
        """Java、JS、TS 仅提供语法事实，组合身份和末级字段传播使用同一公共实现。"""
        sources = (
            ("Main.java", "JAVA-SINK-PROCESS", '''class Worker {String command;String safe;Worker(String v){this.command=v;this.safe="fixed";}}
class Holder {Worker child;Holder(Worker c){this.child=c;}}
class Envelope {Holder child;Envelope(Holder c){this.child=c;}}
class Main {static void route(){String value=System.getenv("CMD");Worker child=new Worker(value);Holder parent=new Holder(child);Envelope root=new Envelope(parent);MUTATION Runtime.getRuntime().exec(SELECTED);}}'''),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v){this.command=v;this.safe='fixed';}}class Holder{constructor(c){this.child=c;}}function route(){const value=process.env.CMD;let child=new Worker(value);let parent=new Holder(child);let root=new Holder(parent);MUTATION exec(SELECTED);}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{command:string;safe:string;constructor(v:string){this.command=v;this.safe='fixed';}}class Holder<T>{child:T;constructor(c:T){this.child=c;}}function route(){const value=process.env.CMD;let child=new Worker(value);let parent=new Holder(child);let root=new Holder(parent);MUTATION exec(SELECTED);}"),
        )
        for path, rule, source in sources:
            for selected, mutation, expected in (
                ("root.child.child.command", "", True),
                ("root.child.child.safe", "", False),
                ("child.command", 'root.child.child.command="fixed";', False),
                ("root.child.child.command", 'child=new Worker("fixed");', True),
                ("root.child.child.command", 'parent.child=new Worker("fixed");', False),
                ("root.child.child.command", 'parent.child=new Worker(value);', True),
            ):
                with self.subTest(path=path, selected=selected, mutation=mutation):
                    pack = self.analyze({path: source.replace("SELECTED", selected).replace("MUTATION", mutation)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_rebinding_sharing_cycles_and_branch_ambiguity(self) -> None:
        """共享子对象、父字段重绑定、局部变量重绑定和有界环不混淆对象身份。"""
        for body, expected in (
            ("child=Worker(value)\nparent=Holder(child)\nchild=Worker('fixed')\nos.system(parent.child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nsafe=Worker('fixed')\nparent.child=safe\nos.system(parent.child.command)", False),
            ("child=Worker(value)\nparent=Holder(child)\nsafe=Worker('fixed')\nparent.child=safe\nos.system(child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nother=Holder(child)\nother.child.command='fixed'\nos.system(parent.child.command)", False),
            ("child=Worker(value)\nparent=Holder(child)\nother=Holder(child)\nparent.child=None\nos.system(other.child.command)", True),
            ("child=Worker(value)\nparent=Holder(child)\nroot=Holder(parent)\nparent.child=root\nos.system(parent.child.child.child.child.command)", False),
            ("child=Worker(value)\nparent=Holder(child)\nsafe=Worker('fixed')\nif flag:\n parent.child=safe\nos.system(parent.child.command)", False),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)


class NestedObjectContractTests(unittest.TestCase):
    """公共引用后端独立于语言语法；值复制、深度前沿与对象环不能变成错误的精确结果。"""

    def graph(self, path: str, *, policy: str = "reference") -> FunctionProgramGraph:
        """构造带自引用字段的最小公共 CFG；不调用任何语言前端。"""
        from test import test_value_identities as values
        template = values.ValueIdentityTests().graph()
        nodes = {
            "entry": ProgramGraphNode(node_id="entry", method_id="f", language="neutral", kind="method_entry", definitions=["item"]),
            "link": ProgramGraphNode(node_id="link", method_id="f", language="neutral", kind="assignment",
                definitions=["item.child"], uses=["item"], value_transfers=[ProgramValueTransfer(
                    output_variable="item.child", input_variables=["item"], transfer_kind="assignment", reference_source="item")]),
            "sink": ProgramGraphNode(node_id="sink", method_id="f", language="neutral", kind="call", uses=[path]),
        }
        edges = [ProgramGraphEdge(edge_id=str(index), source=source, target=target, kind="cfg")
                 for index, (source, target) in enumerate(zip(nodes, list(nodes)[1:]))]
        return template.model_copy(update={"entry_node_id": "entry", "exit_node_id": "sink", "nodes": nodes,
                                           "edges": edges, "object_assignment": policy})

    def test_cycles_are_bounded_by_requested_path_not_heap_expansion(self) -> None:
        """自引用可以定位末级字段，但深路径必须诊断而非无限递归或保留部分精确槽。"""
        for depth, expected in ((3, True), (16, True), (17, False)):
            with self.subTest(depth=depth):
                name = "item" + ".child" * (depth - 1) + ".command"
                graph = self.graph(name)
                diagnostics = FlowDiagnostics()
                instance = LocalInstance("entry", "", "allocation:item", "Node", "item", ("child", "command"))
                references = ObjectReferenceIndex(graph, [instance], diagnostics)
                slot = references.field_binding(graph.nodes["sink"], name, write=False)
                self.assertEqual(expected, slot is not None)
                view = references.bind_fields(graph)
                if expected:
                    self.assertEqual("allocation:item", slot.allocation_id)
                    self.assertEqual("command", slot.field_name)
                else:
                    self.assertNotIn(name, view.nodes["sink"].use_object_slots)
                    self.assertTrue(diagnostics.counts.get("instance_nested_path_budget_exceeded"))

    def test_value_copy_and_unknown_policy_do_not_gain_reference_semantics(self) -> None:
        """Go/C/C++ 后端须先提供可靠引用能力，不能因共用字段协议就把结构体值复制当别名。"""
        for policy in ("value", "unknown"):
            with self.subTest(policy=policy):
                name = "item.child.command"
                graph = self.graph(name, policy=policy)
                instance = LocalInstance("entry", "", "allocation:item", "Node", "item", ("child", "command"))
                references = ObjectReferenceIndex(graph, [instance])
                self.assertIsNone(references.field_binding(graph.nodes["sink"], name, write=False))


if __name__ == "__main__":
    unittest.main()
