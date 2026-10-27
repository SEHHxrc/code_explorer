"""实例方法语法适配：公共类作用域模板，语言子类只声明自身差异。"""

from abc import ABC, abstractmethod
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import field, text

from ..contracts import InstanceMethodContext, ObjectAssignmentKind


class InstanceSyntax(ABC):
    """把语言 AST 规范为实例接收者/所属类/构造器，不处理值流或安全规则。"""

    class_nodes: frozenset[str] = frozenset()
    method_nodes: frozenset[str] = frozenset()
    object_assignment: ObjectAssignmentKind = "unknown"

    def describe(self, node: Any, source: bytes, path: str, scope: tuple[str, ...],
                 name: str, parameters: tuple[str, ...]) -> InstanceMethodContext | None:
        """输入声明、源码和既有身份，返回直接类成员上下文；嵌套函数和静态成员不猜测。"""
        if node.type not in self.method_nodes or not scope or self.is_static(node):
            return None
        parent = node.parent
        while parent is not None and parent.type not in self.class_nodes:
            if parent.type in self.method_nodes:
                return None
            parent = parent.parent
        if parent is None or text(field(parent, "name"), source) != scope[-1]:
            return None
        receiver = self.receiver(parameters)
        return InstanceMethodContext(owner_symbol=ProgramIdentity.symbol_id(path, scope), receiver=receiver,
                                     is_constructor=self.is_constructor(node, name, scope[-1]),
                                     shared_fields=sorted(self.shared_fields(node, source))) if receiver else None

    def shared_fields(self, node: Any, source: bytes) -> set[str]:
        """默认没有经语法确认的共享字段；语言可排除不能按实例隔离的存储。"""
        return set()

    def initializer_call(self, node: Any, source: bytes) -> str:
        """语言可确认显式初始化委派；普通同名函数不默认视为父构造器调用。"""
        return ""

    def is_static(self, node: Any) -> bool:
        """公共静态修饰过滤；访问器和装饰方法不套用普通实例调用模型。"""
        return any(child.type in {"static", "get", "set"} for child in node.children) or any(
            child.type == "modifiers" and any(part.type == "static" for part in child.children)
            for child in node.named_children)

    def receiver(self, parameters: tuple[str, ...]) -> str:
        """默认语言使用隐式 this；参数列表不人为增加一个运行时位置。"""
        return "this"

    @abstractmethod
    def is_constructor(self, node: Any, name: str, owner: str) -> bool:
        """依据本语言构造器声明判定，不按全项目同名函数猜测。"""
        ...


class PythonInstanceSyntax(InstanceSyntax):
    """Python 直接类成员使用显式首形参；装饰方法和特殊分配过程暂不展开。"""

    class_nodes = frozenset({"class_definition"})
    object_assignment: ObjectAssignmentKind = "reference"
    method_nodes = frozenset({"function_definition"})

    def is_static(self, node: Any) -> bool:
        """有装饰器可能改变绑定协议，暂不推断实例身份。"""
        return node.parent is not None and node.parent.type == "decorated_definition"

    def receiver(self, parameters: tuple[str, ...]) -> str:
        """实例接收者是声明的首形参，不要求其字面名称一定是 self。"""
        return parameters[0] if parameters else ""

    def is_constructor(self, node: Any, name: str, owner: str) -> bool:
        """仅 __init__ 作为初始化模型；不把 __new__ 当普通参数透传。"""
        return name == "__init__"

    def initializer_call(self, node: Any, source: bytes) -> str:
        """只标记零参数 super().__init__；真正目标仍须由公共调用索引确认。"""
        callee = field(node, "function")
        owner = field(callee, "object") if callee is not None else None
        arguments = field(owner, "arguments") if owner is not None else None
        return "base" if (callee is not None and callee.type == "attribute" and text(field(callee, "attribute"), source) == "__init__"
            and owner is not None and owner.type == "call" and text(field(owner, "function"), source) == "super"
            and arguments is not None and not arguments.named_children) else ""


class JavaInstanceSyntax(InstanceSyntax):
    """Java 普通实例方法与显式构造器；静态成员沿用普通函数分析。"""

    class_nodes = frozenset({"class_declaration", "record_declaration", "enum_declaration", "interface_declaration"})
    object_assignment: ObjectAssignmentKind = "reference"
    method_nodes = frozenset({"method_declaration", "constructor_declaration"})

    def is_constructor(self, node: Any, name: str, owner: str) -> bool:
        """构造器由语法节点识别，避免把同名普通方法当初始化。"""
        return node.type == "constructor_declaration"

    def member_fields(self, node: Any, source: bytes, *, shared: bool = False) -> set[str]:
        """读取直接类体的字段声明；不展开继承、局部类、字节码或 classpath。"""
        body = node.parent
        if body is None:
            return set()
        return {text(field(item, "name"), source) for declaration in body.named_children
                if declaration.type == "field_declaration" and self.is_static(declaration) == shared
                for item in declaration.named_children if item.type == "variable_declarator"}

    def shared_fields(self, node: Any, source: bytes) -> set[str]:
        """Java static 字段是类共享存储，即使经 this 访问也不能分配到每个实例。"""
        return self.member_fields(node, source, shared=True)


class JavaScriptInstanceSyntax(InstanceSyntax):
    """JS/TS 共用运行时 class 语义；箭头字段、访问器与参数属性另行扩展。"""

    class_nodes = frozenset({"class_declaration", "abstract_class_declaration", "class"})
    object_assignment: ObjectAssignmentKind = "reference"
    method_nodes = frozenset({"method_definition"})

    def is_constructor(self, node: Any, name: str, owner: str) -> bool:
        """仅 class 内 constructor 成员，不推断工厂函数。"""
        return name == "constructor"

    def initializer_call(self, node: Any, source: bytes) -> str:
        """JS/TS 只将 super 语法节点标为父构造器委派。"""
        callee = field(node, "function")
        return "base" if callee is not None and callee.type == "super" else ""


class CppInstanceSyntax(InstanceSyntax):
    """C++ 直接类成员使用隐式 this；普通结构体值复制仍由操作级存储事实决定。"""

    class_nodes = frozenset({"class_specifier", "struct_specifier"})
    method_nodes = frozenset({"function_definition"})
    object_assignment: ObjectAssignmentKind = "unknown"

    def member_fields(self, node: Any, source: bytes) -> set[str]:
        """读取当前直接类声明的非静态字段，局部遮蔽由已有声明索引过滤。"""
        body = node.parent
        if body is None or body.type != "field_declaration_list":
            return set()
        from backend.app.services.syntax_analysis import fields, unwrap_c_declarator

        result = set()
        for declaration in body.named_children:
            if declaration.type != "field_declaration" or self.is_static(declaration):
                continue
            for declarator in fields(declaration, "declarator"):
                name, parameters = unwrap_c_declarator(declarator)
                if name is not None and parameters is None:
                    result.add(text(name, source))
        return result

    def is_constructor(self, node: Any, name: str, owner: str) -> bool:
        """仅类作用域同名初始化函数；初始化列表与重载仍须有独立语义事实支持。"""
        return name == owner


class GoInstanceSyntax(InstanceSyntax):
    """Go 接收者来自方法声明，不把值接收者和指针接收者一概当引用赋值。"""

    object_assignment: ObjectAssignmentKind = "unknown"

    def describe(self, node: Any, source: bytes, path: str, scope: tuple[str, ...],
                 name: str, parameters: tuple[str, ...]) -> InstanceMethodContext | None:
        """接收者类型已经由共享 Go 身份提取器写入 scope；这里只读取接收者变量。"""
        receiver = field(node, "receiver")
        parameter = next(iter(receiver.named_children), None) if receiver is not None else None
        value = text(field(parameter, "name"), source)
        return InstanceMethodContext(owner_symbol=ProgramIdentity.symbol_id(path, scope), receiver=value) \
            if node.type == "method_declaration" and scope and value else None

    def is_constructor(self, node: Any, name: str, owner: str) -> bool:
        """Go 方法不是构造器；普通工厂函数沿已有返回值接口处理。"""
        return False
