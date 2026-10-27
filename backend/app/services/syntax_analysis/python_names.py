"""Python AST 词法名称环境；仅确认导入与遮蔽，不推断运行时对象或任意赋值别名。"""

import ast
import builtins
from contextlib import contextmanager
from dataclasses import dataclass, field
from collections.abc import Iterator


@dataclass
class _Scope:
    """一个词法域中的全部绑定候选；None 表示参数、赋值或项目定义。"""

    kind: str
    parent: "_Scope | None" = None
    names: dict[str, set[str | None]] = field(default_factory=dict)
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)


class PythonNameBindings:
    """函数内先收集局部绑定，避免把后出现的赋值之前的名字当作全局导入。"""

    def __init__(self, tree: ast.AST) -> None:
        """输入既有 AST，不重复读取/解析源码；模块环境仅属于本文件。"""
        self.module = self._collect(tree, "module", None)
        self.current = self.module

    @contextmanager
    def scope(self, node: ast.AST, kind: str) -> Iterator[None]:
        """进入函数/类/表达式作用域；函数闭包查找跳过类命名空间。"""
        previous = self.current
        parent = previous
        if kind == "function":
            while parent.kind == "class" and parent.parent is not None:
                parent = parent.parent
        self.current = self._collect(node, kind, parent)
        try:
            yield
        finally:
            self.current = previous

    def resolve(self, name: str) -> tuple[str, bool]:
        """返回限定名称和已知 API 名遮蔽状态；不把局部值冒充导入模块/内置函数。"""
        scope: _Scope | None = self.current
        if name in scope.globals | scope.nonlocals and name in scope.names:
            return name, True
        if name in scope.globals:
            scope = self.module
        elif name in scope.nonlocals:
            scope = scope.parent
            while scope is not None and scope.kind == "class":
                scope = scope.parent
        while scope is not None:
            candidates = scope.names.get(name)
            if candidates is not None:
                if len(candidates) == 1 and None not in candidates:
                    candidate = next(iter(candidates))
                    if candidate is not None:
                        return candidate, False
                ancestor: _Scope | None = self.current
                imported = False
                while ancestor is not None:
                    imported |= any(value is not None for value in ancestor.names.get(name, ()))
                    ancestor = ancestor.parent
                return name, imported or hasattr(builtins, name)
            scope = scope.parent
        return name, False

    @staticmethod
    def _collect(node: ast.AST, kind: str, parent: _Scope | None) -> _Scope:
        """仅收集当前作用域，嵌套定义只绑定名称，不把其导入泄漏到父域。"""
        result = _Scope(kind=kind, parent=parent)

        def bind(name: str, target: str | None = None) -> None:
            """累计当前词法域的绑定候选；不同来源不采用最后写入者。"""
            result.names.setdefault(name, set()).add(target)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs, *([args.vararg] if args.vararg else []), *([args.kwarg] if args.kwarg else [])]:
                bind(argument.arg)
        body = getattr(node, "body", None)
        pending = list(body) if isinstance(body, list) else [body] if isinstance(body, ast.AST) else [node]
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            pending = list(ast.iter_child_nodes(node))
        while pending:
            item = pending.pop()
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bind(item.name)
                continue
            if isinstance(item, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                continue
            if isinstance(item, ast.Import):
                for alias in item.names:
                    bind(alias.asname or alias.name.split(".", 1)[0], alias.name if alias.asname else alias.name.split(".", 1)[0])
            elif isinstance(item, ast.ImportFrom):
                for alias in item.names:
                    if alias.name != "*" and item.module and not item.level:
                        bind(alias.asname or alias.name, f"{item.module}.{alias.name}")
                    elif alias.name != "*":
                        bind(alias.asname or alias.name)
            elif isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
                bind(item.id)
            elif isinstance(item, ast.ExceptHandler) and item.name:
                bind(item.name)
            elif isinstance(item, ast.Global):
                result.globals.update(item.names)
            elif isinstance(item, ast.Nonlocal):
                result.nonlocals.update(item.names)
            pending.extend(ast.iter_child_nodes(item))
        # 明确对外部作用域写入时，不宣称该函数内仍持有原导入身份。
        for name in result.globals | result.nonlocals:
            if name in result.names:
                result.names[name].add(None)
        return result
