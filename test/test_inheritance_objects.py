"""继承、接口与多继承的公共查询和真实静态传播；不执行目标项目或调用模型。"""

import unittest

from backend.app.services.semantic_index.contracts import BaseTypeFact, TypeFact
from backend.app.services.semantic_index.hierarchy import TypeHierarchy
from test import test_instance_fields as fixtures


class InheritanceObjectTests(unittest.TestCase):
    """祖先声明保留，但只有实际选中的有体实现和正确实例字段参与传播。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def test_python_inherited_initializer_and_method(self) -> None:
        """子类可继承初始化与方法；原实现位置保留，不把实例类型改成祖先类型。"""
        for child, expected in (("pass", True), ("def run(self):\n  os.system('fixed')", False),
                                ("def __init__(self,v):\n  self.command='fixed'", False)):
            with self.subTest(child=child):
                source = "import os\nclass Base:\n def __init__(self,v):\n  self.command=v\n def run(self):\n  os.system(self.command)\nclass Child(Base):\n " + child + "\ndef route():\n value=input()\n obj=Child(value)\n obj.run()\n"
                pack = self.analyze({"app.py": source})
                self.assertEqual(expected, bool(self.verified(pack, "PY-SINK-SHELL")), pack.flow_diagnostics)

    def test_python_multilevel_c3_selects_existing_implementation(self) -> None:
        """C3 与 BFS 选择不同实现时，依赖调用和对象字段消费者必须使用相同顺序。"""
        source = '''import os
class A:
 def __init__(self,v):
  self.command=v
class B(A):
 def run(self):
  os.system(self.command)
class C(A):
 def run(self):
  os.system('fixed')
class D(B):
 pass
class E(D,C):
 pass
def route():
 value=input()
 obj=E(value)
 obj.run()
'''
        pack = self.analyze({"app.py": source})
        self.assertTrue(self.verified(pack, "PY-SINK-SHELL"), pack.flow_diagnostics)

    def test_explicit_parent_initializer_across_languages(self) -> None:
        """父构造器调用复用正常出口字段摘要，后续常量覆盖仍可切断传播。"""
        sources = (
            ("app.py", "PY-SINK-SHELL", "import os\nclass Base:\n def __init__(self,v):\n  self.command=v\n def run(self):\n  os.system(self.command)\nclass Child(Base):\n def __init__(self,v):\n  super().__init__(v)\n  END\ndef route():\n value=input()\n obj=Child(value)\n obj.run()\n", "self.command='fixed'", "pass"),
            ("Main.java", "JAVA-SINK-PROCESS", 'class Base{String command;Base(String v){this.command=v;}void run(){Runtime.getRuntime().exec(this.command);}}class Child extends Base{Child(String v){super(v);END}static void route(){String value=System.getenv("CMD");Child obj=new Child(value);obj.run();}}', 'this.command="fixed";', ""),
            ("app.ts", "JS-SINK-SHELL", "import {exec} from 'node:child_process';class Base{command:string;constructor(v:string){this.command=v;}run(){exec(this.command);}}class Child extends Base{constructor(v:string){super(v);END}}function route(){let value=process.env.CMD;let obj=new Child(value);obj.run();}", "this.command='fixed';", ""),
        )
        for path, rule, source, clean, empty in sources:
            for ending, expected in ((empty, True), (clean, False)):
                with self.subTest(path=path, ending=ending):
                    pack = self.analyze({path: source.replace("END", ending)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)


    def test_cpp_inherited_field_and_method_use_actual_storage(self) -> None:
        """C++ 多个基类的唯一成员可接入字段流；歧义非虚菱形不可强行选一个基类。"""
        for parents, bases, expected in (
            ("struct Base{char *command;void run(){system(command);}};struct Other{int id;};", "Base,Other", True),
            ("struct Root{char *command;void run(){system(this->command);}};struct Base:Root{};struct Other:Root{};", "Base,Other", False),
            ("struct Root{char *command;void run(){system(this->command);}};struct Base:virtual Root{};struct Other:virtual Root{};", "Base,Other", True),
        ):
            with self.subTest(bases=bases, parents=parents):
                source = '#include <cstdlib>\n' + parents + 'struct Child:' + bases + '{};void route(){char *value=getenv("CMD");Child obj;obj.command=value;obj.run();}'
                pack = self.analyze({"app.cpp": source})
                self.assertEqual(expected, bool(self.verified(pack, "C-SINK-SHELL")), pack.flow_diagnostics)

    def test_java_hidden_fields_are_not_merged(self) -> None:
        """子类同名字段不能给父类危险读取提供不存在的值流，未支持的声明槽显式留前沿。"""
        pack = self.analyze({"Main.java": 'class Base{String command;Base(){this.command="fixed";}void run(){Runtime.getRuntime().exec(this.command);}}class Child extends Base{String command;Child(String v){super();this.command=v;}static void route(){String v=System.getenv("CMD");Child obj=new Child(v);obj.run();}}'})
        self.assertFalse(self.verified(pack, "JAVA-SINK-PROCESS"))
        self.assertIn("storage_member_not_resolved", pack.flow_diagnostics["counts"])


class TypeHierarchyTests(unittest.TestCase):
    """公共类型契约的歧义与子对象身份不依赖某语言的污点规则。"""

    def test_cpp_virtual_and_nonvirtual_diamond_are_distinct(self) -> None:
        """非虚菱形有两个 A 子对象，虚基类只共享一个；不能简单按类型名去重。"""
        for virtual, expected in ((False, 2), (True, 1)):
            with self.subTest(virtual=virtual):
                types = {name: TypeFact(symbol_id=name, language="cpp", kind="class", bases=bases) for name, bases in (
                    ("A", []), ("B", [BaseTypeFact(symbol_id="A", virtual=virtual)]),
                    ("C", [BaseTypeFact(symbol_id="A", virtual=virtual)]),
                    ("D", [BaseTypeFact(symbol_id="B"), BaseTypeFact(symbol_id="C")]))}
                hierarchy = TypeHierarchy(types)
                self.assertEqual(expected, len(hierarchy.subobjects("D", "A")))
                self.assertEqual(expected, len(hierarchy.member_owners("D", {"A"})))

    def test_c3_conflict_does_not_choose_an_arbitrary_parent(self) -> None:
        """非法或不完整 MRO 不退回先遇到的同名方法。"""
        types = {name: TypeFact(symbol_id=name, language="python", kind="class",
                               bases=[BaseTypeFact(symbol_id=base) for base in parents]) for name, parents in (
            ("A", []), ("B", []), ("X", ["A", "B"]), ("Y", ["B", "A"]), ("Z", ["X", "Y"]))}
        hierarchy = TypeHierarchy(types)
        self.assertIsNone(hierarchy.order("Z"))
        self.assertEqual((), hierarchy.member_owners("Z", {"A", "B"}))

    def test_nonvirtual_sibling_subobject_is_not_hidden_by_another_branch(self) -> None:
        """B 覆写 A 不会消除 C 支路独立 A 的同名成员；类型层次不是子对象支配证明。"""
        types = {name: TypeFact(symbol_id=name, language="cpp", kind="struct",
                               bases=[BaseTypeFact(symbol_id=base) for base in parents]) for name, parents in (
            ("A", []), ("B", ["A"]), ("C", ["A"]), ("D", ["B", "C"]))}
        hierarchy = TypeHierarchy(types)
        self.assertEqual({"A", "B"}, set(hierarchy.member_owners("D", {"A", "B"})))


if __name__ == "__main__":
    unittest.main()
