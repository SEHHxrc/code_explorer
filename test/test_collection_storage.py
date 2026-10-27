"""固定集合元素复用公共引用、值流与证据交付；不执行目标项目或模型请求。"""

import unittest

from test import test_instance_fields as fixtures
from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphNode
from backend.app.services.value_binding.objects import CollectionElementSlot
from backend.app.services.value_binding.access_paths import canonical_path, parent_path


class CollectionStorageTests(unittest.TestCase):
    """固定键必须按分配身份隔离，别名写入不能串到相邻元素或不同集合。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def python(self, body: str, helper: str = "") -> fixtures.SecurityEvidencePack:
        """从真实源码走生产分析全流程，非手工拼接污点图。"""
        return self.analyze({"app.py": "import os\n" + helper + "\ndef route():\n value=input()\n"
                             + "".join(" " + line + "\n" for line in body.splitlines())})

    def test_static_path_and_typed_element_contract(self) -> None:
        """键内点号、引号与数字保留规范身份，动态索引及 bool 不得冒充固定槽。"""
        self.assertEqual('root["a.b"]', parent_path('root["a.b"][0]'))
        self.assertIsNone(canonical_path("root[index]"))
        self.assertNotEqual(CollectionElementSlot(allocation_id="one", selector=0).value_id,
                            CollectionElementSlot(allocation_id="one", selector="0").value_id)
        with self.assertRaises(ValueError):
            CollectionElementSlot(allocation_id="one", selector=True)
        self.assertEqual({}, ProgramGraphArtifact.model_validate({"schema_version": "1.15"}).functions)
        node = ProgramGraphNode(node_id="element", kind="operation", language="python", method_id="method",
            definition_object_slots={'box[0]': CollectionElementSlot(allocation_id="one", selector=0)})
        restored = ProgramGraphNode.model_validate_json(node.model_dump_json())
        self.assertEqual(node.definition_object_slots, restored.definition_object_slots)

    def test_python_alias_keys_and_overwrites(self) -> None:
        """别名共享元素但不共享相邻键；覆盖、重绑定与独立分配保持流敏感。"""
        for body, expected in (
            ('box={"cmd":value,"safe":"fixed"}\nos.system(box["cmd"])', True),
            ('box={"cmd":value,"safe":"fixed"}\nos.system(box["safe"])', False),
            ('box={"cmd":"fixed"}\nalias=box\nalias["cmd"]=value\nos.system(box["cmd"])', True),
            ('box={"cmd":value}\nalias=box\nalias["cmd"]="fixed"\nos.system(box["cmd"])', False),
            ('box={"cmd":value}\nalias=box\nbox={"cmd":"fixed"}\nos.system(alias["cmd"])', True),
            ('box={"cmd":value}\nother={"cmd":"fixed"}\nos.system(other["cmd"])', False),
            ('box={0:value,"0":"fixed"}\nos.system(box["0"])', False),
            ('box=[value,"fixed"]\nalias=box\nos.system(alias[0])', True),
            ('box=[value,"fixed"]\nalias=box\nos.system(alias[1])', False),
            ('box=[value,"fixed"]\nos.system(box[-2])', True),
            ('box=[value,"fixed"]\nos.system(box[-1])', False),
            ('box={"@instance:abcd":value}\nos.system(box["@instance:abcd"])', True),
            ('box={"cmd":value,"cmd":"fixed"}\nos.system(box["cmd"])', False),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_nested_collections_and_fixed_new_key(self) -> None:
        """嵌套集合逐跳定位子分配，新增固定字典键不污染其他键。"""
        for body, expected in (
            ('box={"child":[value,"fixed"]}\nalias=box["child"]\nos.system(alias[0])', True),
            ('box={"child":[value,"fixed"]}\nalias=box["child"]\nos.system(alias[1])', False),
            ('box={}\nalias=box\nalias["a.b"]=value\nos.system(box["a.b"])', True),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_parameter_writeback_and_factory_return(self) -> None:
        """集合实参和新集合返回复用对象边界；固定元素常量写回应切断旧值。"""
        for body, helper, expected in (
            ('box={"cmd":"fixed"}\nchange(box,value)\nos.system(box["cmd"])', 'def change(obj,value):\n obj["cmd"]=value\n', True),
            ('box={"cmd":value}\nchange(box,"fixed")\nos.system(box["cmd"])', 'def change(obj,value):\n obj["cmd"]=value\n', False),
            ('box=make(value)\nalias=box\nos.system(alias["cmd"])', 'def make(value):\n result={"cmd":value,"safe":"fixed"}\n return result\n', True),
            ('box=make(value)\nos.system(box["safe"])', 'def make(value):\n result={"cmd":value,"safe":"fixed"}\n return result\n', False),
            ('box=make(value)\nos.system(box["cmd"])', 'def make(value):\n return {"cmd":value}\n', True),
            ('box={"cmd":"fixed"}\nalias=identity(box)\nalias["cmd"]=value\nos.system(box["cmd"])', 'def identity(obj):\n return obj\n', True),
            ('box={"cmd":value}\nalias=identity(box)\nalias["cmd"]="fixed"\nos.system(box["cmd"])', 'def identity(obj):\n return obj\n', False),
        ):
            with self.subTest(body=body):
                pack = self.python(body, helper)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_cross_language_fixed_elements(self) -> None:
        """JS/TS 属性键、Java 数组和 Go map/slice 均消费公共元素槽，非各写一套污点引擎。"""
        cases = (
            ('app.js', 'JS-SINK-SHELL', "import {exec} from 'node:child_process';function route(){const value=process.env.CMD;const box={cmd:value,safe:'fixed'};const alias=box;exec(alias.SELECTED);}", (("cmd", True), ("safe", False))),
            ('app.ts', 'JS-SINK-SHELL', "import {exec} from 'node:child_process';function route(){const value=process.env.CMD;const box=[value,'fixed'];const alias=box;exec(alias[SELECTED]);}", (("0", True), ("1", False))),
            ('Main.java', 'JAVA-SINK-PROCESS', 'class Main {static void route(){String value=System.getenv("CMD");String[] box=new String[]{value,"fixed"};String[] alias=box;Runtime.getRuntime().exec(alias[SELECTED]);}}', (("0", True), ("1", False))),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc main(){value:=os.Getenv("CMD");box:=map[string]string{"cmd":value,"safe":"fixed"};alias:=box;exec.Command(alias["SELECTED"])}\n', (("cmd", True), ("safe", False))),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc main(){value:=os.Getenv("CMD");box:=[]string{value,"fixed"};alias:=box;exec.Command(alias[SELECTED])}\n', (("0", True), ("1", False))),
        )
        for filename, rule, source, selections in cases:
            for selected, expected in selections:
                with self.subTest(filename=filename, selected=selected, source=source):
                    pack = self.analyze({filename: source.replace("SELECTED", selected)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_mutation_in_other_languages(self) -> None:
        """跨函数固定元素写回保留语言传参角色，Go 数组按值复制而切片共享元素。"""
        cases = (
            ('app.js', 'JS-SINK-SHELL', "import {exec} from 'node:child_process';function change(obj,value){obj.cmd=value;}function route(){const value=process.env.CMD;const box={cmd:'fixed'};change(box,value);exec(box.cmd);}", True),
            ('Main.java', 'JAVA-SINK-PROCESS', 'class Main{static void change(String[] obj,String value){obj[0]=value;}static void route(){String value=System.getenv("CMD");String[] box={"fixed"};change(box,value);Runtime.getRuntime().exec(box[0]);}}', True),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc change(obj map[string]string,value string){obj["cmd"]=value}\nfunc main(){value:=os.Getenv("CMD");box:=map[string]string{"cmd":"fixed"};change(box,value);exec.Command(box["cmd"])}', True),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc change(obj []string,value string){obj[0]=value}\nfunc main(){value:=os.Getenv("CMD");box:=[]string{"fixed"};change(box,value);exec.Command(box[0])}', True),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc change(obj [1]string,value string){obj[0]=value}\nfunc main(){value:=os.Getenv("CMD");box:=[1]string{"fixed"};change(box,value);exec.Command(box[0])}', False),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc main(){value:=os.Getenv("CMD");box:=[1]string{"fixed"};alias:=box;alias[0]=value;exec.Command(box[0])}', False),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc main(){value:=os.Getenv("CMD");box:=[1]string{value};alias:=box;box[0]="fixed";exec.Command(alias[0])}', True),
        )
        for filename, rule, source, expected in cases:
            with self.subTest(filename=filename, source=source):
                pack = self.analyze({filename: source})
                self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_objects_and_collection_members_use_one_reference_environment(self) -> None:
        """对象字段保存集合、集合保存对象和方法读取嵌套元素必须定位到实际末级存储。"""
        helper = '''class Child:
 def __init__(self,value):
  self.command=value
class Holder:
 def __init__(self,options):
  self.options=options
 def run(self):
  os.system(self.options["cmd"])
'''
        for body, expected in (
            ('box={"cmd":value}\nholder=Holder(box)\nholder.run()', True),
            ('box={"cmd":"fixed"}\nholder=Holder(box)\nalias=holder.options\nalias["cmd"]=value\nholder.run()', True),
            ('child=Child(value)\nbox={"child":child}\nos.system(box["child"].command)', True),
            ('child=Child(value)\nbox={"child":child}\nbox["child"].command="fixed"\nos.system(child.command)', False),
        ):
            with self.subTest(body=body):
                pack = self.python(body, helper)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_full_evidence_retains_alias_and_real_source_positions(self) -> None:
        """JSON 恢复和按 ID 分页不丢失键、别名、原调用位置或新增传播路径。"""
        pack = self.python('box={"cmd":"fixed"}\nalias=box\nalias["cmd"]=value\nos.system(box["cmd"])')
        candidate = self.verified(pack, "PY-SINK-SHELL")[0]
        restored = fixtures.SecurityEvidencePack.model_validate_json(pack.model_dump_json())
        steps = restored.dataflows[candidate.dataflow_id].steps
        self.assertTrue(any(step.expression == "alias=box" and step.object_allocation_id for step in steps))
        self.assertTrue(any('alias["cmd"]=value' in step.expression for step in steps))
        catalog = fixtures.SecurityEvidenceCatalog(restored.model_dump())
        self.assertEqual([step.model_dump() for step in steps], catalog.detail(candidate.candidate_id, section="flow", offset=0, limit=100)["items"])

    def test_unknown_calls_do_not_reuse_old_precise_elements(self) -> None:
        """未知修改和数组重排不能沿用旧元素槽；未建链是分析缺口，不是安全证明。"""
        for operation in ("unknown(box)", "box.clear()", "box.reverse()"):
            with self.subTest(operation=operation):
                pack = self.python('box=[value,"fixed"]\n' + operation + '\nos.system(box[0])')
                self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                self.assertGreater(pack.flow_diagnostics["counts"].get("storage_unknown_effect", 0), 0)

    def test_literal_spread_is_explicitly_incomplete(self) -> None:
        """未知展开只说明已观察成员，不把局部布局提升成完整集合或净化事实。"""
        pack = self.python('box={"cmd":value,**unknown_values}\nos.system(box["cmd"])')
        self.assertGreater(pack.flow_diagnostics["counts"].get("collection_layout_incomplete", 0), 0)
