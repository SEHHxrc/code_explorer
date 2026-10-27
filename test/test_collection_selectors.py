"""有界动态键与未知元素的生产链路回归；不执行项目或调用大模型。"""

import unittest

from test import test_collection_storage as fixtures


class CollectionSelectorTests(unittest.TestCase):
    """常量/有限/未知选择器都不能把键控制性误认作元素值污点。"""

    analyze = fixtures.CollectionStorageTests.analyze
    verified = fixtures.CollectionStorageTests.verified
    python = fixtures.CollectionStorageTests.python

    def test_constant_and_finite_keys(self) -> None:
        """同名变量重赋值、分支合并、三元表达式与常量覆盖均使用操作前键值。"""
        for body, expected in (
            ('box={"cmd":value,"safe":"fixed"}\nkey="cmd"\nos.system(box[key])', True),
            ('box={"cmd":value,"safe":"fixed"}\nkey="safe"\nos.system(box[key])', False),
            ('box={"cmd":value,"safe":"fixed"}\nkey="cmd"\nkey="safe"\nos.system(box[key])', False),
            ('box={"cmd":value,"safe":"fixed"}\nkey="cmd" if flag else "safe"\nos.system(box[key])', True),
            ('box={"cmd":value,"safe":"fixed"}\nif flag:\n key="cmd"\nelse:\n key="safe"\nos.system(box[key])', True),
            ('box={"cmd":value,"safe":"fixed"}\nkey="safe"\nbox[key]="fixed"\nos.system(box["cmd"])', True),
            ('box={"cmd":value}\nkey="cmd"\nbox[key]="fixed"\nos.system(box["cmd"])', False),
            ('box={"cmd":value,"safe":"fixed"}\nkey="cmd" if flag else "safe"\nbox[key]="fixed"\nos.system(box["cmd"])', True),
            ('key="cmd"\nbox={key:value,"safe":"fixed"}\nos.system(box["cmd"])', True),
            ('key="cmd"\nbox={key:value,"safe":"fixed"}\nos.system(box["safe"])', False),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_unknown_reads_writes_and_clean_overwrite(self) -> None:
        """未知写是 may，保留旧值；之后明确固定键覆盖仍可切断该键的旧内容。"""
        for body, expected in (
            ('box={"cmd":value,"safe":"fixed"}\nkey=input()\nos.system(box[key])', True),
            ('box={"cmd":"fixed","safe":"fixed"}\nkey=input()\nos.system(box[key])', False),
            ('box={"cmd":"fixed"}\nkey=input()\nbox[key]=value\nos.system(box["cmd"])', True),
            ('box={"cmd":value}\nkey=input()\nbox[key]="fixed"\nos.system(box["cmd"])', True),
            ('box={"cmd":"fixed"}\nkey=input()\nbox[key]=value\nbox["cmd"]="fixed"\nos.system(box["cmd"])', False),
            ('box={}\nalias=box\nkey=input()\nalias[key]=value\nos.system(box["later"])', True),
            ('box={}\nkey=input()\nbox[key]=input()\nos.system(box["cmd"])', True),
            ('box={"cmd":"fixed"}\nkey=input()\nresult=box[key]\nos.system(result)', False),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)
                self.assertGreater(pack.flow_diagnostics["counts"].get("selector_unknown", 0), 0)

    def test_append_effect_interface_and_bounded_model(self) -> None:
        """只为已知原生可变列表的一次追加编译正常后置写入，别名仍共用原引用环境。"""
        for body, expected in (
            ('box=[]\nbox.append(value)\nos.system(box[0])', True),
            ('box=[]\nbox.append("fixed")\nos.system(box[0])', False),
            ('box=["fixed"]\nalias=box\nalias.append(value)\nos.system(box[1])', True),
            ('box=["fixed"]\nbox.append(value)\nos.system(box[0])', False),
            ('box=["fixed"]\nbox.append(value)\nbox[1]="fixed"\nos.system(box[1])', False),
            ('box=["fixed"]\nbox.append(value)\nos.system(box[-1])', True),
            ('box=[]\nresult=box.append(value)\nos.system(result)', False),
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_unsupported_append_keeps_frontier(self) -> None:
        """循环、多次追加、元组或未知修改后的对象不得靠 append 同名变成支持。"""
        for body in (
            'box=[]\nbox.append(value)\nbox.append("fixed")\nos.system(box[0])',
            'box=[]\nwhile flag:\n box.append(value)\nos.system(box[0])',
            'box=[]\nunknown(box)\nbox.append(value)\nos.system(box[0])',
        ):
            with self.subTest(body=body):
                pack = self.python(body)
                self.assertFalse(self.verified(pack, "PY-SINK-SHELL"))
                self.assertGreater(pack.flow_diagnostics["counts"].get("collection_effect_not_modelled", 0), 0)

    def test_dynamic_parameter_access(self) -> None:
        """跨函数常量/未知选择器仍沿原对象参数接口，不另外推断调用或返回。"""
        for helper, body, expected in (
            ('def change(obj,value):\n key="cmd"\n obj[key]=value\n', 'box={"cmd":"fixed"}\nchange(box,value)\nos.system(box["cmd"])', True),
            ('def change(obj,value,key):\n obj[key]=value\n', 'box={"cmd":"fixed"}\nkey=input()\nchange(box,value,key)\nos.system(box["cmd"])', True),
            ('def read(obj,key):\n os.system(obj[key])\n', 'box={"cmd":value}\nread(box,"cmd")', True),
            ('def forward(value):\n return value\n', 'box={}\nkey="cmd"\nbox[key]=forward(value)\nos.system(box["cmd"])', True),
        ):
            with self.subTest(helper=helper):
                pack = self.python(body, helper)
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_cross_language_selector(self) -> None:
        """Python/Java/Go/JS/TS 只适配表达式语法，共用同一常量键求值器。"""
        cases = (
            ('app.js', 'JS-SINK-SHELL', "import {exec} from 'node:child_process';function route(){const value=process.env.CMD;const box={cmd:value,safe:'fixed'};let key='SELECTED';exec(box[key]);}", (('cmd', True), ('safe', False))),
            ('app.ts', 'JS-SINK-SHELL', "import {exec} from 'node:child_process';function route(){const value=process.env.CMD;const box=[value,'fixed'];let key=SELECTED;exec(box[key]);}", (('0', True), ('1', False))),
            ('Main.java', 'JAVA-SINK-PROCESS', 'class Main{static void route(){String value=System.getenv("CMD");String[] box={value,"fixed"};int index=SELECTED;Runtime.getRuntime().exec(box[index]);}}', (('0', True), ('1', False))),
            ('main.go', 'GO-SINK-PROCESS', 'package main\nimport("os";"os/exec")\nfunc main(){value:=os.Getenv("CMD");box:=map[string]string{"cmd":value,"safe":"fixed"};key:="SELECTED";exec.Command(box[key])}', (('cmd', True), ('safe', False))),
        )
        for filename, rule, source, choices in cases:
            for selected, expected in choices:
                with self.subTest(filename=filename, selected=selected):
                    pack = self.analyze({filename: source.replace('SELECTED', selected)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)
