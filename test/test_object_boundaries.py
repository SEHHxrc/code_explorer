"""工厂可达对象与组合引用写回的公共端到端回归；只解析源码，不运行样例或调用模型。"""

import unittest

from test import test_instance_fields as fixtures
from test import test_nested_objects as nested
from backend.app.services.program_graph import ProgramGraphNode, ProgramValueTransfer
from backend.app.services.security_analysis.flow_analysis.object_contracts import LocalInstance
from backend.app.services.security_analysis.flow_analysis.object_references import ObjectReferenceIndex
from backend.app.services.security_analysis.flow_analysis.object_snapshots import import_snapshot
from backend.app.services.security_analysis.flow_analysis.diagnostics import FlowDiagnostics


class ObjectBoundaryTests(unittest.TestCase):
    """新边界必须保留对象共享、旧别名、调用隔离、字段覆盖及原始证据。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def python(self, helpers: str, body: str) -> fixtures.SecurityEvidencePack:
        """复用生产分析流水线，公共样例只提供直接引用类，不包含样本专属安全提示。"""
        return self.analyze({"app.py": "import os\nclass Worker:\n def __init__(self,v):\n  self.command=v\n  self.safe='fixed'\nclass Holder:\n def __init__(self,c):\n  self.left=c\n  self.right=c\n" + helpers + "\ndef route():\n value=input()\n " + body.replace("\n", "\n ") + "\n"})

    def test_factory_nested_sharing_and_call_contexts(self) -> None:
        """内部子对象随返回导出，共享仍共享；另一次调用与安全兄弟字段不继承旧污点。"""
        helpers = "def make(v):\n child=Worker(v)\n parent=Holder(child)\n return parent\n"
        for body, expected in (
            ("obj=make(value)\nos.system(obj.left.command)", True),
            ("obj=make(value)\nos.system(obj.right.safe)", False),
            ("obj=make(value)\nobj.left.command='fixed'\nos.system(obj.right.command)", False),
            ("first=make(value)\nobj=make('fixed')\nos.system(obj.left.command)", False),
            ("obj=make(value)\nsafe=Worker('fixed')\nobj.left=safe\nos.system(obj.right.command)", True),
        ):
            with self.subTest(body=body):
                pack = self.python(helpers, body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_factory_can_return_internal_child_or_alias(self) -> None:
        """返回根可为内部字段或其局部别名；返回关系改变不等于深复制父对象。"""
        for ending in ("return parent.left", "alias=parent.left\n return alias", "alias=child\n return alias"):
            with self.subTest(ending=ending):
                helper = "def make(v):\n child=Worker(v)\n parent=Holder(child)\n " + ending + "\n"
                pack = self.python(helper, "obj=make(value)\nos.system(obj.command)")
                self.assertTrue(self.verified(pack, "PY-SINK-SHELL"), pack.flow_diagnostics)

    def test_reference_language_factories_share_same_heap_export(self) -> None:
        """Java/JS/TS 仅适配语法；构造器、内部共享和返回边界不各自写一套引擎。"""
        sources = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker{String command;Worker(String v){this.command=v;}}class Holder{Worker left;Worker right;Holder(Worker c){this.left=c;this.right=c;}}class Main{static Holder make(String v){Worker c=new Worker(v);Holder p=new Holder(c);return p;}static void route(){String v=System.getenv("CMD");Holder p=make(v);END Runtime.getRuntime().exec(p.right.command);}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v){this.command=v;}}class Holder{constructor(c){this.left=c;this.right=c;}}function make(v){let c=new Worker(v);return new Holder(c);}function route(){let v=process.env.CMD;let p=make(v);END exec(p.right.command);}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{command:string;constructor(v:string){this.command=v;}}class Holder{left:Worker;right:Worker;constructor(c:Worker){this.left=c;this.right=c;}}function make(v:string):Holder{let c=new Worker(v);return new Holder(c);}function route(){let v=process.env.CMD;let p=make(v);END exec(p.right.command);}"),
        )
        for path, rule, source in sources:
            for ending, expected in (("", True), ('p.left.command="fixed";', False)):
                with self.subTest(path=path, ending=ending):
                    pack = self.analyze({path: source.replace("END", ending)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_parameter_reference_replacement_preserves_old_aliases(self) -> None:
        """替换 parent.left 只改关系，不清掉旧子对象，也不改共享该对象的 right。"""
        helper = "def replace(p,other):\n p.left=other\n"
        for body, expected in (
            ("old=Worker(value)\np=Holder(old)\nother=Worker('fixed')\nreplace(p,other)\nos.system(p.left.command)", False),
            ("old=Worker(value)\np=Holder(old)\nother=Worker('fixed')\nreplace(p,other)\nos.system(p.right.command)", True),
            ("old=Worker(value)\np=Holder(old)\nother=Worker('fixed')\nreplace(p,other)\nos.system(old.command)", True),
            ("old=Worker('fixed')\np=Holder(old)\nother=Worker(value)\nreplace(p,other)\nos.system(p.left.command)", True),
        ):
            with self.subTest(body=body):
                pack = self.python(helper, body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_replacement_with_new_object_transports_values(self) -> None:
        """被调函数新建子对象，调用方接入其字段值与身份；安全构造与第二次调用隔离。"""
        for creation in ("p.left=Worker(v)", "child=Worker(v)\n p.left=child"):
            helper = "def replace(p,v):\n " + creation + "\n"
            for argument, expected in (("value", True), ("'fixed'", False)):
                with self.subTest(creation=creation, argument=argument):
                    pack = self.python(helper, "old=Worker('fixed')\np=Holder(old)\nreplace(p," + argument + ")\nos.system(p.left.command)")
                    self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_factory_after_replacement_and_repeated_calls_use_current_topology(self) -> None:
        """工厂内先替换再返回；调用后的别名进入第二次调用时必须读取新关系而非预编译旧关系。"""
        helper = "def replace(p,other):\n p.left=other\ndef make(v):\n old=Worker('fixed')\n p=Holder(old)\n child=Worker(v)\n replace(p,child)\n return p\n"
        for body, expected in (
            ("p=make(value)\nos.system(p.left.command)", True),
            ("p=make(value)\nos.system(p.right.command)", False),
            ("old=Worker('fixed')\np=Holder(old)\nother=Worker(value)\nreplace(p,other)\nq=Holder(old)\nreplace(q,p.left)\nos.system(q.left.command)", True),
            ("old=Worker(value)\np=Holder(old)\nother=Worker('fixed')\nreplace(p,other)\nq=Holder(old)\nreplace(q,p.left)\nos.system(q.left.command)", False),
        ):
            with self.subTest(body=body):
                pack = self.python(helper, body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_reference_replacements_across_languages(self) -> None:
        """Java/JS/TS 的自由函数/静态方法及 this 接收者复用同一引用写回。"""
        sources = (
            ("Main.java", "JAVA-SINK-PROCESS", 'class Worker{String command;Worker(String v){this.command=v;}}class Holder{Worker left;Holder(Worker c){this.left=c;}void replace(Worker c){this.left=c;}}class Main{static void route(){String v=System.getenv("CMD");Worker old=new Worker("fixed");Holder p=new Holder(old);Worker next=new Worker(ARG);p.replace(next);Runtime.getRuntime().exec(p.left.command);}}'),
            ("app.js", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{constructor(v){this.command=v;}}class Holder{constructor(c){this.left=c;}replace(c){this.left=c;}}function route(){let v=process.env.CMD;let old=new Worker('fixed');let p=new Holder(old);let next=new Worker(ARG);p.replace(next);exec(p.left.command);}"),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Worker{command:string;constructor(v:string){this.command=v;}}class Holder{left:Worker;constructor(c:Worker){this.left=c;}replace(c:Worker){this.left=c;}}function route(){let v=process.env.CMD;let old=new Worker('fixed');let p=new Holder(old);let next=new Worker(ARG);p.replace(next);exec(p.left.command);}"),
        )
        for path, rule, source in sources:
            for argument, expected in (("v", True), ('"fixed"', False)):
                with self.subTest(path=path, argument=argument):
                    pack = self.analyze({path: source.replace("ARG", argument)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_go_pointer_factory_and_reference_replacement(self) -> None:
        """Go 指针字段不是值结构体深复制，工厂导出与指针参数写回共用快照和引用环境。"""
        base = 'package main\nimport("os";"os/exec")\ntype Worker struct{Command string}\ntype Holder struct{Left *Worker;Right *Worker}\n'
        helper = 'func replace(p *Holder, other *Worker){p.Left=other}\nfunc make(v string)*Holder{c:=&Worker{Command:v};p:=&Holder{Left:c,Right:c};return p}\n'
        for body, expected in (
            ('p:=make(v);exec.Command(p.Left.Command).Run()', True),
            ('p:=make(v);p.Left.Command="fixed";exec.Command(p.Right.Command).Run()', False),
            ('old:=&Worker{Command:"fixed"};p:=&Holder{Left:old};next:=&Worker{Command:v};replace(p,next);exec.Command(p.Left.Command).Run()', True),
        ):
            with self.subTest(body=body):
                source = (base + helper + 'func route(){v:=os.Getenv("CMD");' + body + '}').replace("make(", "build(")
                pack = self.analyze({"app.go": source})
                self.assertEqual(expected, bool(self.verified(pack, "GO-SINK-PROCESS")), pack.flow_diagnostics)

    def test_reference_sources_can_be_subobjects_or_shared_external_parameters(self) -> None:
        """替换可来自 another.left；新对象组合外部实参时保留原对象，不克隆或清空共享字段。"""
        cases = (
            ("def replace(p,other):\n p.left=other.left\n", "other_child=Worker(value)\nother=Holder(other_child)\nreplace(p,other)\nos.system(p.left.command)", True),
            ("def replace(p,other):\n wrapper=Holder(other)\n p.left=wrapper\n", "other=Worker(value)\nreplace(p,other)\nos.system(p.left.right.command)", True),
            ("def replace(p,other):\n wrapper=Holder(other)\n p.left=wrapper\n", "other=Worker(value)\nreplace(p,other)\np.left.left.command='fixed'\nos.system(other.command)", False),
        )
        for helper, ending, expected in cases:
            with self.subTest(helper=helper, ending=ending):
                pack = self.python(helper, "old=Worker('fixed')\np=Holder(old)\n" + ending)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_c_pointer_field_replacement_is_not_struct_value_copy(self) -> None:
        """C/C++ 指针字段直接写回共享目标，值结构体传参仍不改调用方的引用字段。"""
        source = '#include <stdlib.h>\nstruct Worker{char *command;};struct Holder{struct Worker *left;};void replace(struct Holder *p,struct Worker *other){p->left=other;}void route(){char *v=getenv("CMD");struct Worker old;old.command="fixed";struct Holder p;p.left=&old;struct Worker next;next.command=v;replace(&p,&next);system(p.left->command);}'
        for path in ("app.c", "app.cpp"):
            with self.subTest(path=path):
                pack = self.analyze({path: source})
                self.assertTrue(self.verified(pack, "C-SINK-SHELL"), pack.flow_diagnostics)

    def test_reference_evidence_retains_original_assignment_and_call(self) -> None:
        """值没有跨过程重新产生时，关系写回仍交付真实赋值位置和原调用边，不只留生成节点。"""
        pack = self.python("def replace(p,other):\n p.left=other\n", "old=Worker('fixed')\np=Holder(old)\nother=Worker(value)\nreplace(p,other)\nos.system(p.left.command)")
        candidate = self.verified(pack, "PY-SINK-SHELL")[0]
        flow = pack.dataflows[candidate.dataflow_id]
        self.assertTrue(any(step.symbol == "app.py::replace" and step.expression == "p.left=other" and step.reference_edge_id
                            for step in flow.steps))
        self.assertTrue(any(pack.call_edges[key].target == "app.py::replace" for key in flow.call_edge_ids))
        restored = fixtures.SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        catalog = fixtures.SecurityEvidenceCatalog(restored.model_dump())
        delivered, offset = [], 0
        while True:
            page = catalog.detail(candidate.candidate_id, section="flow", offset=offset, limit=4)
            delivered.extend(page["items"])
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        self.assertEqual([step.model_dump() for step in flow.steps], delivered)
        pack = self.python("def replace(p,other):\n p.left=other\n", "old=Worker('fixed')\np=Holder(old)\nother=Worker(value)\nreplace(p,other)\nq=Holder(old)\nreplace(q,p.left)\nos.system(q.left.command)")
        flow = pack.dataflows[self.verified(pack, "PY-SINK-SHELL")[0].dataflow_id]
        calls = [key for key in flow.call_edge_ids if pack.call_edges[key].target == "app.py::replace"]
        self.assertEqual(2, len(calls), "前一次引用改变也必须交付，不能只剩最后一次 helper 调用")

    def test_conditional_reference_change_is_not_a_proven_clean_overwrite(self) -> None:
        """仅一个分支替换时保留前沿，不能把候选关系当确定净化；两个分支一致时可统一绑定。"""
        helper = "def replace(p,other):\n if flag:\n  p.left=other\n"
        pack = self.python(helper, "old=Worker(value)\np=Holder(old)\nother=Worker('fixed')\nreplace(p,other)\nos.system(old.command)")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"))
        self.assertIn("object_reference_effect_not_resolved", pack.flow_diagnostics["counts"])
        helper = "def replace(p,other):\n if flag:\n  p.left=other\n else:\n  p.left=other\n"
        pack = self.python(helper, "old=Worker('fixed')\np=Holder(old)\nother=Worker(value)\nreplace(p,other)\nos.system(p.left.command)")
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"), pack.flow_diagnostics)
    def test_forwarded_factory_and_repeated_new_object_effects_are_ordered(self) -> None:
        """工厂新导入的引用先完成调用写回，再导出上一层；同一 helper 的多个调用不能复用旧摘要。"""
        helper = "def replace(p,v):\n p.left=Worker(v)\ndef inner(v):\n child=Worker('fixed')\n return Holder(child)\ndef outer(v):\n p=inner(v)\n replace(p,v)\n return p\n"
        for body, expected in (
            ("p=outer(value)\nos.system(p.left.command)", True),
            ("p=outer(value)\nos.system(p.right.command)", False),
            ("old=Worker('fixed')\np=Holder(old)\nq=Holder(old)\nreplace(p,value)\nreplace(q,'fixed')\nos.system(p.left.command)", True),
            ("old=Worker('fixed')\np=Holder(old)\nq=Holder(old)\nreplace(p,value)\nreplace(q,'fixed')\nos.system(q.left.command)", False),
        ):
            with self.subTest(body=body):
                pack = self.python(helper, body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)


class ObjectSnapshotContractTests(unittest.TestCase):
    """传输契约不依赖 AST/语言规则，必须有界且不把共享/环变成重复对象。"""

    def test_cycle_snapshot_and_materialization_preserve_identity(self) -> None:
        """自引用只导出一个对象；导入另一调用产生新身份，但内部环不复制。"""
        graph = nested.NestedObjectContractTests().graph("item.child.command")
        original = graph.model_dump_json()
        source = LocalInstance("entry", "", "allocation:item", "Node", "item", ("child", "command"))
        resolver = ObjectReferenceIndex(graph, [source])
        snapshot = resolver.snapshot(graph.nodes["sink"], "item", max_objects=1)
        self.assertIsNotNone(snapshot)
        self.assertEqual(1, len(snapshot.objects))
        self.assertEqual(snapshot.root_id, snapshot.references[0].target_id)
        imported = import_snapshot(snapshot, graph.nodes["entry"], "copy", context_id="new-call")
        view = graph.model_copy(update={"nodes": {**graph.nodes, "entry": imported.node}})
        receiver = ObjectReferenceIndex(view, list(imported.instances))
        actual = receiver.field_binding(view.nodes["sink"], "copy.child.command", write=False)
        self.assertEqual(imported.instances[0].allocation_id, actual.allocation_id)
        self.assertNotEqual(source.allocation_id, actual.allocation_id)
        self.assertEqual(original, graph.model_dump_json())

    def test_snapshot_does_not_silently_truncate_reachable_objects(self) -> None:
        """超过对象预算时整份快照不可用，不能丢子对象后继续宣称关系完整。"""
        graph = nested.NestedObjectContractTests().graph("item.child.command")
        nodes = dict(graph.nodes)
        nodes["entry"] = nodes["entry"].model_copy(update={"definitions": ["item", "child"]})
        nodes["link"] = nodes["link"].model_copy(update={"uses": ["child"], "value_transfers": [ProgramValueTransfer(
            output_variable="item.child", input_variables=["child"], reference_source="child", transfer_kind="assignment")]})
        graph = graph.model_copy(update={"nodes": nodes})
        instances = [LocalInstance("entry", "", "allocation:item", "Parent", "item", ("child",)),
                     LocalInstance("entry", "", "allocation:child", "Child", "child", ("command",))]
        diagnostics = FlowDiagnostics()
        resolver = ObjectReferenceIndex(graph, instances, diagnostics)
        self.assertIsNone(resolver.snapshot(nodes["sink"], "item", max_objects=1))
        self.assertIn("object_snapshot_not_resolved", diagnostics.counts)
        self.assertEqual(2, len(resolver.snapshot(nodes["sink"], "item", max_objects=2).objects))

    def test_ambiguous_reference_and_legacy_nodes_remain_explicit(self) -> None:
        """未知分支不挑一个目标；旧节点没有新字段时默认空，不能凭恢复操作补造引用。"""
        graph = nested.NestedObjectContractTests().graph("item.child.command")
        nodes = dict(graph.nodes)
        nodes["link"] = nodes["link"].model_copy(update={"certainty": "may"})
        graph = graph.model_copy(update={"nodes": nodes})
        source = LocalInstance("entry", "", "allocation:item", "Node", "item", ("child", "command"))
        resolver = ObjectReferenceIndex(graph, [source])
        self.assertIsNone(resolver.snapshot(nodes["sink"], "item"))
        legacy = ProgramGraphNode(node_id="old", method_id="old", kind="operation", language="neutral")
        self.assertEqual({}, legacy.object_reference_bindings)


if __name__ == "__main__":
    unittest.main()
