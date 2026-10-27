"""结构体值复制、指针共享和标量取址的公共传播回归；只分析源码。"""

import unittest
from test import test_instance_fields as fixtures


class StorageSemanticsTests(unittest.TestCase):
    """C/C++/Go 使用同一存储环境，不能用字段名称碰巧相同代替真实存储身份。"""

    analyze = fixtures.InstanceFieldTests.analyze
    verified = fixtures.InstanceFieldTests.verified

    def test_struct_value_copy_is_not_alias(self) -> None:
        """值复制后的标量字段独立，旧对象上的指针仍指向原存储。"""
        sources = (
            ("app.c", "C-SINK-SHELL", '#include <stdlib.h>\nstruct Worker{char *command;char *safe;};void route(){char *value=getenv("CMD");struct Worker a;a.command=value;a.safe="fixed";struct Worker b=a;MUTATION system(SELECTED);}'),
            ("app.cpp", "C-SINK-SHELL", '#include <cstdlib>\nstruct Worker{char *command;char *safe;};void route(){char *value=getenv("CMD");Worker a;a.command=value;a.safe="fixed";Worker b=a;MUTATION system(SELECTED);}'),
            ("app.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\ntype Worker struct{Command string;Safe string}\nfunc route(){value:=os.Getenv("CMD");a:=Worker{Command:value,Safe:"fixed"};b:=a;MUTATION exec.Command(SELECTED).Run()}'),
        )
        for path, rule, source in sources:
            for selected, mutation, expected in (("b.command", "", True), ("b.safe", "", False),
                ("a.command", 'b.command="fixed";', True), ("b.command", 'b.command="fixed";', False),
                ("b.command", 'a.command="fixed";', True)):
                if path.endswith(".go"):
                    selected = selected.replace("command", "Command").replace("safe", "Safe")
                    mutation = mutation.replace("command", "Command")
                with self.subTest(path=path, selected=selected, mutation=mutation):
                    pack = self.analyze({path: source.replace("MUTATION", mutation).replace("SELECTED", selected)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_struct_pointer_writes_share_target_not_copies(self) -> None:
        """&a 与 b=a 不是同一种绑定；指针写入可见于 a，不能改写已经复制的 b。"""
        sources = (
            ("app.c", "C-SINK-SHELL", '#include <stdlib.h>\nstruct Worker{char *command;};void route(){char *value=getenv("CMD");struct Worker a;a.command=value;struct Worker b=a;struct Worker *p=&a;p->command="fixed";system(SELECTED);}'),
            ("app.cpp", "C-SINK-SHELL", '#include <cstdlib>\nstruct Worker{char *command;};void route(){char *value=getenv("CMD");Worker a;a.command=value;Worker b=a;Worker *p=&a;p->command="fixed";system(SELECTED);}'),
            ("app.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\ntype Worker struct{Command string}\nfunc route(){value:=os.Getenv("CMD");a:=Worker{Command:value};b:=a;p:=&a;p.Command="fixed";exec.Command(SELECTED).Run()}'),
        )
        for path, rule, source in sources:
            for selected, expected in (("a.command", False), ("b.command", True), ("p->command", False)):
                if path.endswith(".go"):
                    selected = selected.replace("command", "Command").replace("->", ".")
                with self.subTest(path=path, selected=selected):
                    pack = self.analyze({path: source.replace("SELECTED", selected)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_addressed_scalar_and_dereference_share_value_slot(self) -> None:
        """标量单元、其中的值与指针地址分离，解引用读写绑定到真实单元。"""
        sources = (
            ("app.c", "C-SINK-SHELL", '#include <stdlib.h>\nvoid route(){char *value=getenv("CMD");char *x="fixed";char **p=&x;*p=value;END system(SELECTED);}'),
            ("app.cpp", "C-SINK-SHELL", '#include <cstdlib>\nvoid route(){char *value=getenv("CMD");char *x="fixed";char **p=&x;*p=value;END system(SELECTED);}'),
            ("app.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\nfunc route(){value:=os.Getenv("CMD");x:="fixed";p:=&x;*p=value;END exec.Command(SELECTED).Run()}'),
        )
        for path, rule, source in sources:
            for selected, ending, expected in (("x", "", True), ("*p", "", True), ("x", '*p="fixed";', False)):
                with self.subTest(path=path, selected=selected, ending=ending):
                    pack = self.analyze({path: source.replace("SELECTED", selected).replace("END", ending)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_value_parameter_and_pointer_writeback_are_different(self) -> None:
        """同一公共字段接口：传值修改副本，指针/引用修改实参存储，读取均可进入实际实现。"""
        sources = (
            ("app.c", "C-SINK-SHELL", '#include <stdlib.h>\nstruct Worker{char *command;};void change(struct Worker PTR p,char *v){p ACCESS command=v;}void route(){char *value=getenv("CMD");struct Worker a;a.command="fixed";change(ADDRESS a,value);system(a.command);}'),
            ("app.cpp", "C-SINK-SHELL", '#include <cstdlib>\nstruct Worker{char *command;};void change(Worker PTR p,char *v){p ACCESS command=v;}void route(){char *value=getenv("CMD");Worker a;a.command="fixed";change(ADDRESS a,value);system(a.command);}'),
            ("app.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\ntype Worker struct{Command string}\nfunc change(p PTR Worker,v string){p.Command=v}\nfunc route(){value:=os.Getenv("CMD");a:=Worker{Command:"fixed"};change(ADDRESS a,value);exec.Command(a.Command).Run()}'),
        )
        for path, rule, source in sources:
            for pointer, expected in ((False, False), (True, True)):
                with self.subTest(path=path, pointer=pointer):
                    actual = source.replace("PTR", "*" if pointer else "").replace("ACCESS", "->" if pointer else ".").replace("ADDRESS", "&" if pointer else "")
                    pack = self.analyze({path: actual})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_scalar_pointer_parameter_writes_original_cell(self) -> None:
        """取址实参明确绑定原变量单元，而非把指针地址本身当作污点值。"""
        sources = (
            ("app.c", "C-SINK-SHELL", '#include <stdlib.h>\nvoid change(char **p,char *v){*p=v;}void route(){char *v=getenv("CMD");char *x="fixed";change(&x,v);system(x);}'),
            ("app.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\nfunc change(p *string,v string){*p=v}\nfunc route(){v:=os.Getenv("CMD");x:="fixed";change(&x,v);exec.Command(x).Run()}'),
        )
        for path, rule, source in sources:
            with self.subTest(path=path):
                pack = self.analyze({path: source})
                self.assertTrue(self.verified(pack, rule), pack.flow_diagnostics)


    def test_pointer_field_copy_is_shallow_and_embedded_value_is_independent(self) -> None:
        """结构体副本共享指针字段目标，内嵌结构体副本则独立，不能一概深复制或一概别名。"""
        sources = (
            ("app.c", "C-SINK-SHELL", '#include <stdlib.h>\nstruct Child{char *command;};struct Parent{struct Child child;struct Child *ref;};void route(){char *v=getenv("CMD");struct Child shared;shared.command=v;struct Parent a;a.child.command=v;a.ref=&shared;struct Parent b=a;shared.command="fixed";a.child.command="fixed";system(SELECTED);}'),
            ("app.go", "GO-SINK-PROCESS", 'package main\nimport("os";"os/exec")\ntype Child struct{Command string}\ntype Parent struct{Child Child;Ref *Child}\nfunc route(){v:=os.Getenv("CMD");shared:=Child{Command:v};var a Parent;a.Child.Command=v;a.Ref=&shared;b:=a;shared.Command="fixed";a.Child.Command="fixed";exec.Command(SELECTED).Run()}'),
        )
        for path, rule, source in sources:
            for selected, expected in (("b.child.command", True), ("b.ref->command", False), ("a.child.command", False)):
                if path.endswith(".go"):
                    selected = selected.replace("child", "Child").replace("command", "Command").replace("ref", "Ref").replace("->", ".")
                with self.subTest(path=path, selected=selected):
                    pack = self.analyze({path: source.replace("SELECTED", selected)})
                    self.assertEqual(expected, bool(self.verified(pack, rule)), pack.flow_diagnostics)

    def test_unmodelled_pointer_call_does_not_reuse_stale_field(self) -> None:
        """未知函数可能改写存储，不能将调用前字段当调用后事实；不等于判定它已经安全。"""
        source = '#include <stdlib.h>\nstruct Worker{char *command;};void route(){char *v=getenv("CMD");struct Worker a;a.command=v;unknown(&a);system(a.command);}'
        pack = self.analyze({"app.c": source})
        self.assertFalse(self.verified(pack, "C-SINK-SHELL"))
        self.assertIn("storage_unknown_effect", pack.flow_diagnostics["counts"])

    def test_go_receiver_and_cpp_reference_use_same_writeback(self) -> None:
        """Go 值接收者隔离副本，指针接收者与 C++ 引用沿公共正常出口写回。"""
        for receiver, expected in (("Worker", False), ("*Worker", True)):
            with self.subTest(receiver=receiver):
                source = 'package main\nimport("os";"os/exec")\ntype Worker struct{Command string}\nfunc(w ' + receiver + ') Change(v string){w.Command=v}\nfunc route(){v:=os.Getenv("CMD");a:=Worker{Command:"fixed"};a.Change(v);exec.Command(a.Command).Run()}'
                pack = self.analyze({"app.go": source})
                self.assertEqual(expected, bool(self.verified(pack, "GO-SINK-PROCESS")), pack.flow_diagnostics)
        source = '#include <cstdlib>\nstruct Worker{char *command;};void change(Worker &p,char *v){p.command=v;}void route(){char *v=getenv("CMD");Worker a;a.command="fixed";change(a,v);system(a.command);}'
        pack = self.analyze({"app.cpp": source})
        self.assertTrue(self.verified(pack, "C-SINK-SHELL"), pack.flow_diagnostics)

    def test_framework_output_struct_is_field_sensitive(self) -> None:
        """项目布局和输出规则同时存在时，HTTP 绑定写入成员，字段常量覆盖不能被整对象 Source 绕过。"""
        source = 'package main\nimport("github.com/gin-gonic/gin";"os/exec")\ntype Payload struct{Command string}\nfunc handler(c *gin.Context){var payload Payload;c.ShouldBindJSON(&payload);exec.Command(payload.Command);payload.Command="fixed";exec.Command(payload.Command)}'
        pack = self.analyze({"app.go": source})
        hits = self.verified(pack, "GO-SINK-PROCESS")
        self.assertEqual(1, len(hits), pack.flow_diagnostics)
        self.assertNotIn("storage_unknown_effect", pack.flow_diagnostics["counts"])


if __name__ == "__main__":
    unittest.main()
