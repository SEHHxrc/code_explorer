"""将 Python AST 转换为与规则无关的 SecurityProgramIR。"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis.python_names import PythonNameBindings

from ..ir import (
    IRAccess,
    IRCall,
    IRCondition,
    IRDecorator,
    IRExpression,
    IRFunction,
    IRLocation,
    IRParameter,
    SecurityProgramIR,
)
from .base import LanguageFrontend
from .python_callbacks import collect_python_value_boundaries


class PythonSecurityFrontend(LanguageFrontend):
    """只负责 Python 语法解析和规范化 IR，不包含安全规则。"""

    language = "python"
    extensions = frozenset({".py", ".pyi"})

    def __init__(self, *, max_file_bytes: int = 2 * 1024 * 1024) -> None:
        """输入单文件上限，初始化无规则语言前端。"""
        self.max_file_bytes = max_file_bytes

    def build(
        self,
        project_root: str | Path,
        *,
        file_paths: list[str] | None = None,
    ) -> SecurityProgramIR:
        """解析指定 Python 文件；未指定时使用受控目录发现作为兼容回退。"""
        root = Path(project_root).resolve()
        paths = self.normalized_paths(root, file_paths)
        program = SecurityProgramIR(language="python", files_considered=len(paths))
        rebound_names: dict[str, set[str]] = {}
        for relative in paths:
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
                if target.stat().st_size > self.max_file_bytes:
                    program.failures.append({"path": relative, "reason": "file_too_large"})
                    continue
                source = target.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=relative, type_comments=True)
            except (OSError, SyntaxError, ValueError) as exc:
                program.failures.append({
                    "path": relative,
                    "reason": "python_parse_error",
                    "detail": type(exc).__name__,
                })
                continue
            visitor = _PythonIRVisitor(relative, tree)
            visitor.visit(tree)
            program.functions.extend(visitor.functions)
            program.calls.extend(visitor.calls)
            program.conditions.extend(visitor.conditions)
            program.accesses.extend(visitor.accesses)
            rebound_names.update(visitor.rebound_names)
            program.files_scanned += 1
        program.value_boundaries.extend(collect_python_value_boundaries(program, rebound_names))
        return program

class _PythonIRVisitor(ast.NodeVisitor):
    """把单文件 AST 转换成安全规则可复用的规范化事实。"""

    def __init__(self, path: str, tree: ast.AST) -> None:
        """输入路径和语法树，预建导入别名和调用赋值目标索引。"""
        self.path = path
        self.bindings = PythonNameBindings(tree)
        self.assigned_values = self._collect_assigned_values(tree)
        self.scope_stack: list[str] = []
        self.functions: list[IRFunction] = []
        self.calls: list[IRCall] = []
        self.conditions: list[IRCondition] = []
        self.accesses: list[IRAccess] = []
        self.rebound_names: dict[str, set[str]] = {}

    @property
    def symbol(self) -> str:
        """返回与依赖分析器一致的当前作用域 FQN。"""
        return ProgramIdentity.symbol_id(self.path, self.scope_stack)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """维护类作用域并扫描类成员。"""
        for expression in [*node.bases, *node.decorator_list, *(item.value for item in node.keywords)]:
            self.visit(expression)
        self.scope_stack.append(node.name)
        with self.bindings.scope(node, "class"):
            for statement in node.body:
                self.visit(statement)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """提取同步函数 IR 并扫描函数体。"""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """提取异步函数 IR 并扫描函数体。"""
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """构造函数、参数和装饰器 IR。"""
        symbol = ProgramIdentity.symbol_id(self.path, self.scope_stack + [node.name])
        self.rebound_names[symbol] = {
            child.id for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del))
        }
        parameters = tuple(
            IRParameter(
                name=argument.arg,
                annotation=self._parameter_annotation(argument.annotation),
                default_call=self._qualified_name(default) if isinstance(default, ast.Call) else "",
                location=self._location(argument),
                kind=("positional_only" if argument in node.args.posonlyargs else "keyword_only" if argument in node.args.kwonlyargs else "variadic_positional" if argument is node.args.vararg else "variadic_keyword" if argument is node.args.kwarg else "positional_or_keyword"),
            )
            for argument, default in self._parameters_with_defaults(node.args)
        )
        decorators: list[IRDecorator] = []
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call is not None else decorator
            decorators.append(IRDecorator(
                qualified_name=self._qualified_name(target),
                arguments=tuple(self._expression(arg) for arg in (call.args if call else [])),
            ))
        self.functions.append(IRFunction(
            symbol=symbol,
            name=node.name,
            parameters=parameters,
            decorators=tuple(decorators),
            location=self._location(node),
        ))
        # 默认值、装饰器和注解在声明侧求值，不能套用函数体的局部遮蔽。
        for expression in [*node.decorator_list, *node.args.defaults,
                           *(item for item in node.args.kw_defaults if item is not None),
                           *(item.annotation for item, _ in self._parameters_with_defaults(node.args) if item.annotation is not None),
                           *([node.returns] if node.returns is not None else [])]:
            self.visit(expression)
        self.scope_stack.append(node.name)
        with self.bindings.scope(node, "function"):
            for statement in node.body:
                self.visit(statement)
        self.scope_stack.pop()

    def _parameter_annotation(self, node: ast.AST | None) -> str:
        """规范化导入别名与 Annotated 的类型，不按类名子串排除请求数据。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return self._parameter_annotation(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                return node.value
        if isinstance(node, ast.Subscript) and self._qualified_name(node.value) in {
            "typing.Annotated", "typing_extensions.Annotated", "Annotated",
        }:
            value = node.slice.elts[0] if isinstance(node.slice, ast.Tuple) else node.slice
            return self._parameter_annotation(value)
        if isinstance(node, (ast.Name, ast.Attribute)):
            return self._qualified_name(node)
        return self._node_text(node)

    def visit_Call(self, node: ast.Call) -> None:
        """提取调用名称、参数、关键字、接收者和赋值目标。"""
        receiver = self._node_text(node.func.value) if isinstance(node.func, ast.Attribute) else ""
        arguments: list[ast.expr] = []
        for argument in node.args:
            if isinstance(argument, ast.Starred) and isinstance(argument.value, (ast.List, ast.Tuple)) and not any(isinstance(item, ast.Starred) for item in argument.value.elts):
                arguments.extend(argument.value.elts)
            else:
                arguments.append(argument)
        keywords: list[tuple[str, IRExpression]] = []
        for keyword in node.keywords:
            value = keyword.value
            if keyword.arg is None and isinstance(value, ast.Dict) and all(isinstance(key, ast.Constant) and isinstance(key.value, str) for key in value.keys):
                keywords.extend((str(key.value), self._expression(item)) for key, item in zip(value.keys, value.values) if isinstance(key, ast.Constant))
            else:
                keywords.append((keyword.arg or "**", self._expression(value)))
        self.calls.append(IRCall(
            qualified_name=self._qualified_name(node.func),
            callsite_id=ProgramIdentity.callsite_id(
                self.path,
                int(getattr(node, "lineno", 1)),
                int(getattr(node, "col_offset", 0)) + 1,
                int(getattr(node, "end_lineno", getattr(node, "lineno", 1)) or 1),
                int(getattr(node, "end_col_offset", 0)) + 1,
            ),
            symbol=self.symbol,
            location=self._location(node),
            arguments=tuple(self._expression(arg) for arg in arguments),
            keywords=tuple(keywords),
            assigned_targets=self.assigned_values.get(id(node), ()),
            receiver=receiver,
            callee_shadowed=self._callee_shadowed(node.func),
        ))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """把 ``os.environ[...]`` 转换为显式读取 IR。"""
        if self._qualified_name(node.value) == "os.environ":
            self.accesses.append(IRAccess(
                qualified_name="os.environ[]",
                symbol=self.symbol,
                access_kind="read",
                location=self._location(node),
                assigned_targets=self.assigned_values.get(id(node), ()),
            ))
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        """记录条件表达式，但不声明其一定保护后续 Sink。"""
        self.conditions.append(IRCondition(
            kind="if",
            symbol=self.symbol,
            expression=self._expression(node.test),
            location=self._location(node.test),
        ))
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        """记录断言表达式。"""
        self.conditions.append(IRCondition(
            kind="assert",
            symbol=self.symbol,
            expression=self._expression(node.test),
            location=self._location(node),
        ))
        self.generic_visit(node)

    def _qualified_name(self, node: ast.AST | None) -> str:
        """解析表达式限定名称并应用导入别名。"""
        if node is None:
            return ""
        if isinstance(node, ast.Call):
            return self._qualified_name(node.func)
        if isinstance(node, ast.Name):
            name, shadowed = self.bindings.resolve(node.id)
            return f"<shadowed>.{node.id}" if shadowed else name
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    def _expression(self, node: ast.AST) -> IRExpression:
        """将 AST 表达式转换为后续 Def-Use 可消费的轻量表示。"""
        identifiers = tuple(sorted({
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }))
        is_literal = isinstance(node, ast.Constant)
        return IRExpression(
            text=self._node_text(node)[:1000],
            identifiers=identifiers,
            is_literal=is_literal,
            literal=node.value if is_literal else None,
            kind=type(node).__name__.lower(),
            contains_call=any(isinstance(child, ast.Call) for child in ast.walk(node)),
        )

    def _location(self, node: ast.AST) -> IRLocation:
        """转换 Python AST 的零基列号为统一的一基源码位置。"""
        line = max(1, int(getattr(node, "lineno", 1)))
        column = int(getattr(node, "col_offset", 0)) + 1
        end_line = int(getattr(node, "end_lineno", line) or line)
        end_column = int(getattr(node, "end_col_offset", column) or column) + 1
        return IRLocation(
            path=self.path,
            line=line,
            column=max(1, column),
            end_line=max(line, end_line),
            end_column=max(1, end_column),
            location_id=ProgramIdentity.location_id(
                self.path,
                line,
                max(1, column),
                max(line, end_line),
                max(1, end_column),
            ),
        )

    def _callee_shadowed(self, node: ast.AST) -> bool:
        """判断调用根是否遮蔽已有导入/内置名，不按属性后缀猜测 API 身份。"""
        while isinstance(node, (ast.Attribute, ast.Call)):
            node = node.value if isinstance(node, ast.Attribute) else node.func
        return isinstance(node, ast.Name) and self.bindings.resolve(node.id)[1]

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """lambda 参数不泄漏；这里只规范名称，不新增闭包或调用调度语义。"""
        for expression in [*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)]:
            self.visit(expression)
        with self.bindings.scope(node, "function"):
            self.visit(node.body)

    def _visit_comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp) -> None:
        """首个迭代源在外层求值，其余名称位于独立推导式域；不建立调度/闭包边。"""
        self.visit(node.generators[0].iter)
        with self.bindings.scope(node, "function"):
            for index, generator in enumerate(node.generators):
                if index:
                    self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)
            for expression in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.visit(expression)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    @classmethod
    def _collect_assigned_values(cls, tree: ast.AST) -> dict[int, tuple[str, ...]]:
        """索引赋值右值对应目标，并对等长简单解构按位置配对。"""
        result: dict[int, list[str]] = {}
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif (isinstance(node, ast.AnnAssign) and node.value is not None) or isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]
            if value is None:
                continue
            bindings: list[tuple[list[ast.AST], ast.AST]] = [(targets, value)]
            if (
                len(targets) == 1
                and isinstance(targets[0], (ast.Tuple, ast.List))
                and isinstance(value, (ast.Tuple, ast.List))
                and len(targets[0].elts) == len(value.elts)
            ):
                bindings = [([target], item) for target, item in zip(targets[0].elts, value.elts)]
            for bound_targets, bound_value in bindings:
                names = tuple(dict.fromkeys(
                    name
                    for target in bound_targets
                    for name in cls._target_names(target)
                ))
                for child in ast.walk(bound_value):
                    assigned = result.setdefault(id(child), [])
                    assigned.extend(name for name in names if name not in assigned)
        return {key: tuple(names) for key, names in result.items()}

    @classmethod
    def _target_names(cls, node: ast.AST) -> list[str]:
        """提取简单、属性和解构赋值目标的文本名称。"""
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            return [cls._node_text(node)]
        if isinstance(node, (ast.Tuple, ast.List)):
            return [name for item in node.elts for name in cls._target_names(item)]
        return []

    @staticmethod
    def _parameters_with_defaults(arguments: ast.arguments) -> list[tuple[ast.arg, ast.expr | None]]:
        """按声明顺序返回参数与默认值。"""
        positional = list(arguments.posonlyargs) + list(arguments.args)
        defaults: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults))
        defaults.extend(arguments.defaults)
        result = list(zip(positional, defaults))
        result.extend(zip(arguments.kwonlyargs, arguments.kw_defaults))
        if arguments.vararg:
            result.append((arguments.vararg, None))
        if arguments.kwarg:
            result.append((arguments.kwarg, None))
        return result

    @staticmethod
    def _node_text(node: ast.AST | None) -> str:
        """将 AST 节点还原为有界源码表达式。"""
        if node is None:
            return ""
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            return ""
