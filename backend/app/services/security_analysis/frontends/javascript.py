"""JS/TS 安全语法适配：导入、词法作用域、调用和属性读写，不自行判定漏洞。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterator

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import extensions_for_language, field, text
from backend.app.services.syntax_analysis.source_units import SourceUnit
from backend.app.services.syntax_analysis.vue_bindings import vue_model_target, vue_setup_bindings, vue_template_expression_nodes, vue_template_names
from backend.app.services.syntax_analysis.callback_inputs import CallbackParameterInput
from backend.app.services.syntax_analysis.javascript_inputs import javascript_callback_inputs, project_uses_react
from backend.app.services.syntax_analysis.javascript import (
    JAVASCRIPT_FUNCTION_NODES,
    javascript_access_name,
    javascript_function,
    javascript_parameters,
)
from backend.app.services.syntax_analysis.javascript_bindings import (
    javascript_pattern,
    javascript_value,
    resolve_javascript_binding,
)
from backend.app.services.value_binding import BindingValue, StructuredBindingResolver

from ..ir import (
    IRAccess,
    IRCall,
    IRCondition,
    IRExpression,
    IRFunction,
    IRLocation,
    IRParameter,
    SecurityProgramIR,
)
from .treesitter import TreeSitterSecurityFrontend
from .javascript_state import collect_javascript_value_boundaries

_CALLS = {"call_expression", "new_expression"}
_FUNCTIONS = {
    "arrow_function",
    "function_expression",
    "function_declaration",
    "method_definition",
}
_GLOBALS = {
    "location",
    "document",
    "window",
    "process",
    "eval",
    "Function",
    "fetch",
    "localStorage",
    "sessionStorage",
}


class JavaScriptSecurityFrontend(TreeSitterSecurityFrontend):
    """将 JavaScript/JSX 源码转换成通用安全 IR，复用公共 CFG/DFG。"""

    language = "javascript"
    parser_name = "javascript"
    extensions = extensions_for_language(language)

    def collect_file(
        self, program: SecurityProgramIR, path: str, source: bytes, root: Any
    ) -> None:
        """追加一个文件的函数、调用、读写和覆盖缺口；不执行项目代码。"""
        _JavaScriptIRCollector(program, path, source, root).collect(root)

    def collect_file_context(
        self, program: SecurityProgramIR, path: str, unit: SourceUnit,
        root: Any, project_root: Path,
    ) -> None:
        """利用项目内静态依赖识别自动 JSX 运行库，不执行配置或共享可变状态。"""
        self._collect_unit(program, path, unit, root, project_uses_react(project_root, path))

    def collect_unit(
        self, program: SecurityProgramIR, path: str, unit: SourceUnit, root: Any
    ) -> None:
        """Vue 内联脚本与 v-html 共享原始坐标，模板只提取事实，不运行组件。"""
        self._collect_unit(program, path, unit, root, False)

    def _collect_unit(
        self, program: SecurityProgramIR, path: str, unit: SourceUnit,
        root: Any, react: bool,
    ) -> None:
        """将静态项目线索传入本文件收集器，再合并脚本及模板事实。"""
        collector = _JavaScriptIRCollector(program, path, unit.source, root, react=react and not path.endswith(".vue"))
        if unit.setup:
            if "defineProps" not in collector.global_shadows:
                collector.aliases.setdefault("defineProps", "vue.defineProps")
            collector.module_symbol = ProgramIdentity.symbol_id(path, ("vue_setup",))
            collector.collect(root)
            symbol = collector.module_symbol
            lines = unit.source.split(b"\n")
            location = IRLocation(
                path,
                1,
                1,
                len(lines),
                len(lines[-1]) + 1,
                ProgramIdentity.location_id(path, 1, 1, len(lines), len(lines[-1]) + 1),
            )
            program.functions.append(IRFunction(symbol, "vue_setup", (), (), location))
        else:
            collector.collect(root)
            symbol = path
        if unit.template_source:
            mutable, refs = vue_setup_bindings(root, unit.source)
            tree = self.parser_pool.get(self.parser_name).parse(unit.template_source)
            if tree.root_node.has_error:
                collector._failure(root, "vue_template_partial_parse_error")
                return
            collector.source = unit.template_source
            for binding, expression in vue_template_expression_nodes(unit, tree.root_node):
                if binding.directive.startswith("v-model"):
                    target = vue_model_target(expression, unit.template_source, mutable, refs) if unit.setup else ""
                    if target:
                        program.accesses.append(IRAccess(
                            "vue.template.v-model", symbol, "read",
                            collector._location(expression), assigned_targets=(target,),
                            binding_certainty="may",
                        ))
                    else:
                        collector._failure(expression, "vue_v_model_target_not_modeled")
                else:
                    program.accesses.append(
                        IRAccess(
                            "vue.template.v-html",
                            symbol,
                            "write",
                            collector._location(expression),
                            value_identifiers=vue_template_names(collector._identifiers(expression), refs),
                        )
                    )


class TypeScriptSecurityFrontend(JavaScriptSecurityFrontend):
    """TypeScript/TSX 复用 JS 运行时语义，只在参数中保留类型注解。"""

    language = "typescript"
    parser_name = "typescript"
    extensions = extensions_for_language(language)


class _JavaScriptIRCollector:
    """规范化可观察的 ESM/CommonJS API，隔离参数/局部变量对同名 API 的遮蔽。"""

    def __init__(
        self, program: SecurityProgramIR, path: str, source: bytes, root: Any,
        *, react: bool = False,
    ) -> None:
        """索引文件作用域绑定与路由注册，为后续函数扫描提供有限类型证据。"""
        self.program, self.path, self.source = (
            program,
            ProgramIdentity.file_id(path),
            source,
        )
        self.module_symbol = self.path
        self.aliases = self._aliases(root)
        self.global_shadows = self._declared(root, descend_functions=False) - set(
            self.aliases
        )
        writes = self._written(root)
        self.global_shadows.update(writes)
        self.aliases = {
            name: value for name, value in self.aliases.items() if name not in writes
        }
        self.react = react or any(
            value == "react" or value.startswith(("react.", "react/"))
            for value in self.aliases.values()
        )
        self.callback_inputs: dict[tuple[int, int], list[CallbackParameterInput]] = {}
        self.instances = self._instances(root, self.aliases, self.global_shadows)
        self.instances = {
            name: value for name, value in self.instances.items() if name not in writes
        }
        self._register_inputs(root, self.aliases, self.instances, self.global_shadows)

    def _register_inputs(
        self, root: Any, aliases: dict[str, str], instances: dict[str, str],
        shadows: set[str],
    ) -> tuple[CallbackParameterInput, ...]:
        """缓存当前作用域的注册事实，以精确回调范围而非名字绑定形参。"""
        bindings = javascript_callback_inputs(
            root, self.source, react=self.react,
            qualify=lambda node: self._qualified(node, aliases, instances, shadows),
        )
        for binding in bindings:
            bucket = self.callback_inputs.setdefault(binding.callback_span, [])
            if binding not in bucket:
                bucket.append(binding)
                for reason in binding.limitations:
                    self.program.failures.append({
                        "path": self.path,
                        "line": self.source[:binding.registration_span[0]].count(b"\n") + 1,
                        "reason": reason,
                    })
        return bindings

    def collect(self, root: Any, scope: tuple[str, ...] = ()) -> None:
        """按词法作用域递归收集函数；模块顶层行为目前只索引导入和注册。"""
        if root.type in {"class_declaration", "abstract_class_declaration", "class"}:
            name = self._text(field(root, "name"))
            scope = scope + (name,) if name else scope
        if root.type in JAVASCRIPT_FUNCTION_NODES:
            parts = javascript_function(root, self.source)
            if parts is not None:
                name, target = parts
                body = field(target, "body")
                if body is not None:
                    self._function(root, target, name, body, scope)
                    for child in body.named_children:
                        self.collect(child, scope + (name,))
                    return
        for child in root.named_children:
            self.collect(child, scope)
        if not scope and root.type == "program":
            # 路由注册在模块作用域，不应归入某个业务函数。
            self._body(
                root,
                self.module_symbol,
                self.aliases,
                self.instances,
                self.global_shadows,
            )

    def _function(
        self, node: Any, target: Any, name: str, body: Any, scope: tuple[str, ...]
    ) -> None:
        """生成共享函数身份；只把路由 request 对象的指定字段视为 HTTP Source。"""
        parameters = javascript_parameters(target, self.source)
        symbol = ProgramIdentity.symbol_id(self.path, scope + (name,))
        self.program.functions.append(
            IRFunction(
                symbol=symbol,
                name=name,
                decorators=(),
                location=self._location(node),
                parameters=tuple(
                    IRParameter(
                        p,
                        annotation,
                        "",
                        self._location(item),
                        javascript_pattern(item, self.source, self.path),
                    )
                    for p, annotation, item in parameters
                ),
            )
        )
        local = (
            self._declared(body, descend_functions=False)
            | self._written(body)
            | {
                projection.output.split(".", 1)[0]
                for _, _, item in parameters
                for projection in StructuredBindingResolver()
                .resolve(
                    javascript_pattern(item, self.source, self.path), BindingValue()
                )
                .projections
            }
        )
        shadows = self.global_shadows | local
        aliases = {
            key: value for key, value in self.aliases.items() if key not in local
        }
        if "require" not in local:
            aliases.update(self._aliases(body))
        writes = self._written(body)
        aliases = {name: value for name, value in aliases.items() if name not in writes}
        instances = {
            key: value for key, value in self.instances.items() if key not in local
        }
        instances.update(self._instances(body, aliases, shadows))
        instances = {
            name: value for name, value in instances.items() if name not in writes
        }
        callback_inputs = self.callback_inputs.get((target.start_byte, target.end_byte), [])
        input_types = {binding.type_name for binding in callback_inputs if binding.parameter_position == 0}
        component = not callback_inputs and self.react and name[:1].isupper() and any(
            item.type in {"jsx_element", "jsx_self_closing_element"}
            for item in self._walk(body, skip_functions=True)
        )
        if component and parameters:
            instances[parameters[0][0]] = "react.Component.props"
        if len(input_types) == 1 and parameters:
            input_type = next(iter(input_types))
            if input_type in {"react.DOM.InputEvent", "node.IncomingMessage", "express.Request"} and parameters[0][0] not in writes:
                instances[parameters[0][0]] = input_type
            elif input_type == "node.IncomingMessage.bodyChunk":
                self.program.accesses.append(IRAccess(
                    input_type, symbol, "read", self._location(parameters[0][2]),
                    parameter_seed=parameters[0][0],
                ))
        registrations = self._register_inputs(body, aliases, instances, shadows)
        boundaries, gaps = collect_javascript_value_boundaries(
            body, self.source, self.path, self._location(node), registrations,
            component=component,
            qualify=lambda value: self._qualified(value, aliases, instances, shadows),
            location=self._location,
        )
        self.program.value_boundaries.extend(boundaries)
        for reason in gaps:
            self._failure(node, reason)
        for index, (p, _, item) in enumerate(parameters):
            pattern = javascript_pattern(item, self.source, self.path)
            if pattern.kind == "rest":
                self._failure(item, "javascript_rest_parameter_arguments_not_modeled")
            result = StructuredBindingResolver().resolve(
                pattern, BindingValue(variables=(p,))
            )
            for reason in result.diagnostics:
                self._failure(item, reason)
            if (
                index == 0
                and input_types == {"express.Request"}
                and pattern.kind != "name"
            ):
                for projection in result.projections:
                    selected = next(
                        (
                            part
                            for part in (
                                "query",
                                "body",
                                "params",
                                "headers",
                                "cookies",
                            )
                            if any(
                                value.startswith(p + "." + part)
                                for value in projection.inputs
                            )
                        ),
                        None,
                    )
                    if selected:
                        self.program.accesses.append(
                            IRAccess(
                                f"express.Request.{selected}",
                                symbol,
                                "read",
                                self._location(item),
                                parameter_seed=projection.output,
                            )
                        )
            elif index == 0 and input_types == {"react.DOM.InputEvent"} and pattern.kind != "name":
                for projection in result.projections:
                    selected = next((part for part in ("target.value", "currentTarget.value") if p + "." + part in projection.inputs), None)
                    if selected:
                        self.program.accesses.append(IRAccess(
                            "react.DOM.InputEvent." + selected, symbol, "read",
                            self._location(item), parameter_seed=projection.output,
                        ))
            elif index == 0 and component and pattern.kind != "name":
                for projection in result.projections:
                    self.program.accesses.append(
                        IRAccess(
                            "react.Component.props",
                            symbol,
                            "read",
                            self._location(item),
                            parameter_seed=projection.output,
                        )
                    )
        self._body(body, symbol, aliases, instances, shadows)

    def _body(
        self,
        node: Any,
        symbol: str,
        aliases: dict[str, str],
        instances: dict[str, str],
        shadows: set[str],
    ) -> None:
        """收集当前函数操作；跳过嵌套函数，以免跨作用域传播污点。"""
        if node.type in _FUNCTIONS or (
            node.type == "variable_declarator"
            and javascript_function(node, self.source)
        ):
            return
        if node.type in {"variable_declarator", "assignment_expression"}:
            result = resolve_javascript_binding(
                field(node, "name") or field(node, "left"),
                field(node, "value") or field(node, "right"),
                self.source,
                self.path,
            )
            for reason in result.diagnostics:
                self._failure(node, reason)
        if node.type in _CALLS:
            callee = field(node, "function") or field(node, "constructor")
            qualified = self._qualified(callee, aliases, instances, shadows)
            arguments = field(node, "arguments")
            items = list(arguments.named_children) if arguments is not None else []
            options = items[-1] if items and items[-1].type == "object" else None
            keywords = []
            if options is not None:
                for pair in options.named_children:
                    if pair.type == "pair":
                        keywords.append(
                            (
                                self._text(field(pair, "key")).strip("'\""),
                                self._expression(field(pair, "value")),
                            )
                        )
            if any(item.type == "spread_element" for item in items):
                self._failure(node, "javascript_spread_arguments_not_modeled")
            dynamic_options = qualified in {
                "child_process.spawn",
                "child_process.spawnSync",
                "child_process.execFile",
                "child_process.execFileSync",
            } and (
                (
                    len(items) > 2
                    and items[2].type
                    not in {"object", "arrow_function", "function_expression"}
                )
                or (
                    len(items) == 2
                    and items[1].type
                    not in {"object", "array", "arrow_function", "function_expression"}
                )
            )
            if dynamic_options:
                self._failure(node, "javascript_dynamic_process_options_not_modeled")
            location = self._location(node)
            self.program.calls.append(
                IRCall(
                    qualified_name=qualified,
                    symbol=symbol,
                    location=location,
                    callsite_id=ProgramIdentity.callsite_id(
                        self.path,
                        location.line,
                        location.column,
                        location.end_line,
                        location.end_column,
                    ),
                    arguments=tuple(self._expression(item) for item in items),
                    keywords=tuple(keywords),
                    assigned_targets=self._targets(node),
                    receiver=self._text(field(callee, "object")),
                    unknown_keywords=dynamic_options,
                )
            )
            if (
                qualified in {"react.createElement", "vue.h"}
                and len(items) >= 2
                and items[0].type == "string"
                and self._text(items[0]).strip("'\"")[:1].islower()
            ):
                props = javascript_value(items[1], self.source, self.path)
                if qualified == "react.createElement":
                    selected = next(
                        (
                            item.value
                            for item in props.members
                            if item.selector == "dangerouslySetInnerHTML"
                        ),
                        None,
                    )
                    if selected is not None:
                        html = next(
                            (
                                item.value
                                for item in selected.members
                                if item.selector == "__html"
                            ),
                            None,
                        )
                        identifiers = (
                            html.variables
                            if html is not None
                            else tuple(name + ".__html" for name in selected.variables)
                        )
                        self.program.accesses.append(
                            IRAccess(
                                "react.DOM.dangerouslySetInnerHTML",
                                symbol,
                                "write",
                                self._location(node),
                                value_identifiers=identifiers,
                            )
                        )
                else:
                    selected = next(
                        (
                            item.value
                            for item in props.members
                            if item.selector == "innerHTML"
                        ),
                        None,
                    )
                    if selected is not None:
                        self.program.accesses.append(
                            IRAccess(
                                "vue.render.innerHTML",
                                symbol,
                                "write",
                                self._location(node),
                                value_identifiers=selected.variables,
                            )
                        )
        if node.type == "jsx_attribute":
            attribute = node.named_children[0] if node.named_children else None
            parent = node.parent
            tag = self._text(field(parent, "name"))
            if (
                self._text(attribute) == "dangerouslySetInnerHTML"
                and tag
                and tag[0].islower()
            ):
                expression = next(
                    (
                        item
                        for item in node.named_children
                        if item.type == "jsx_expression"
                    ),
                    None,
                )
                value = (
                    expression.named_children[0]
                    if expression is not None and expression.named_children
                    else None
                )
                if value is not None:
                    resolved = javascript_value(value, self.source, self.path)
                    selected = next(
                        (
                            item.value
                            for item in resolved.members
                            if item.selector == "__html"
                        ),
                        None,
                    )
                    identifiers = (
                        selected.variables
                        if selected is not None
                        else tuple(name + ".__html" for name in resolved.variables)
                    )
                    self.program.accesses.append(
                        IRAccess(
                            "react.DOM.dangerouslySetInnerHTML",
                            symbol,
                            "write",
                            self._location(node),
                            value_identifiers=identifiers,
                        )
                    )
        if node.type in {"member_expression", "subscript_expression"}:
            parent = node.parent
            writing = (
                parent is not None
                and parent.type
                in {"assignment_expression", "augmented_assignment_expression"}
                and field(parent, "left") == node
            )
            qualified = self._qualified(node, aliases, instances, shadows)
            rhs = field(parent, "right") if writing else None
            outer = (
                parent is not None
                and parent.type in {"member_expression", "subscript_expression"}
                and self._qualified(parent, aliases, instances, shadows) == qualified
            )
            if not outer:
                self.program.accesses.append(
                    IRAccess(
                        qualified_name=qualified,
                        symbol=symbol,
                        location=self._location(node),
                        access_kind="write" if writing else "read",
                        assigned_targets=self._targets(node),
                        value_identifiers=self._identifiers(rhs if writing else node),
                    )
                )
        if node.type == "if_statement":
            condition = field(node, "condition")
            if condition is not None:
                self.program.conditions.append(
                    IRCondition(
                        "if",
                        symbol,
                        self._expression(condition),
                        self._location(condition),
                    )
                )
        for child in node.named_children:
            self._body(child, symbol, aliases, instances, shadows)

    def _qualified(
        self,
        node: Any,
        aliases: dict[str, str],
        instances: dict[str, str],
        shadows: set[str],
    ) -> str:
        """仅用已观察导入/工厂或未遮蔽平台全局规范 API；动态属性不猜测。"""
        if node is None:
            return ""
        if node.type in {"member_expression", "subscript_expression"}:
            receiver = self._qualified(
                field(node, "object"), aliases, instances, shadows
            )
            prop = field(node, "property") or field(node, "index")
            if node.type == "subscript_expression" and receiver in {
                "process.argv",
                "process.env",
                "react.Component.props",
                "node.IncomingMessage.headers",
            }:
                return receiver
            if prop is None or (
                node.type == "subscript_expression" and prop.type != "string"
            ):
                return ""
            name = self._text(prop).strip("'\"")
            if receiver.startswith("express.Request.") or receiver in {
                "process.env",
                "process.argv",
                "react.Component.props",
                "node.IncomingMessage.url",
                "node.IncomingMessage.headers",
            }:
                return receiver
            if receiver == "express.Request" and name in {
                "query",
                "body",
                "params",
                "headers",
                "cookies",
            }:
                return f"{receiver}.{name}"
            if receiver == "node.IncomingMessage" and name in {"url", "headers"}:
                return f"{receiver}.{name}"
            if receiver in {"DOM.Element", "document.body", "document.documentElement"}:
                return f"DOM.Element.{name}"
            if receiver == "window" and name in {"location", "document"}:
                return name
            return f"{receiver}.{name}" if receiver else ""
        raw = self._text(node)
        if raw in aliases:
            return aliases[raw]
        if raw in instances:
            return instances[raw]
        if raw in {"http", "https", "fs", "child_process", "express", "react", "vue", "axios"}:
            # 这些不是平台全局；不能让遮蔽后的词法名字重新匹配同名库 API。
            return f"local.{raw}"
        if raw in _GLOBALS and raw in shadows:
            return f"local.{raw}"
        return raw

    def _aliases(self, root: Any) -> dict[str, str]:
        """读取 ESM import 和静态 CommonJS require；不执行或下载依赖。"""
        result: dict[str, str] = {}
        require_shadowed = "require" in self._declared(root, descend_functions=False)
        for node in self._walk(root, skip_functions=True):
            if node.type == "import_statement":
                module = (
                    self._text(field(node, "source")).strip("'\"").removeprefix("node:")
                )
                module = "fs.promises" if module == "fs/promises" else module
                clause = next(
                    (
                        item
                        for item in node.named_children
                        if item.type == "import_clause"
                    ),
                    None,
                )
                if clause is None:
                    continue
                for item in clause.named_children:
                    if item.type == "identifier":
                        result[self._text(item)] = module
                    elif item.type == "namespace_import":
                        result[self._text(item.named_children[-1])] = module
                    elif item.type == "named_imports":
                        for spec in item.named_children:
                            name = self._text(field(spec, "name"))
                            alias = self._text(field(spec, "alias")) or name
                            if name:
                                result[alias] = f"{module}.{name}"
            if node.type != "variable_declarator":
                continue
            value, target = field(node, "value"), field(node, "name")
            if (
                value is None
                or target is None
                or value.type != "call_expression"
                or self._text(field(value, "function")) != "require"
            ):
                continue
            # 自定义 require 不是 CommonJS 模块加载器。
            if require_shadowed:
                continue
            args = field(value, "arguments")
            if (
                args is None
                or len(args.named_children) != 1
                or args.named_children[0].type != "string"
            ):
                continue
            module = (
                self._text(args.named_children[0]).strip("'\"").removeprefix("node:")
            )
            module = "fs.promises" if module == "fs/promises" else module
            if target.type == "identifier":
                result[self._text(target)] = module
            elif target.type == "object_pattern":
                for item in target.named_children:
                    key = field(item, "key") or item
                    local = field(item, "value") or key
                    result[self._text(local)] = f"{module}.{self._text(key)}"
        return result

    def _instances(
        self, root: Any, aliases: dict[str, str], shadows: set[str]
    ) -> dict[str, str]:
        """仅从显式 Express/DOM 工厂调用记录接收者，不根据变量名称推断类型。"""
        result: dict[str, str] = {}
        for node in self._walk(root, skip_functions=True):
            if node.type != "variable_declarator":
                continue
            value, target = field(node, "value"), field(node, "name")
            if value is None or target is None or value.type != "call_expression":
                continue
            callee = self._qualified(field(value, "function"), aliases, result, shadows)
            if callee in {"express", "express.Router"}:
                result[self._text(target)] = "express.Application"
            elif callee in {
                "document.getElementById",
                "document.querySelector",
                "document.createElement",
            }:
                result[self._text(target)] = "DOM.Element"
        return result

    def _targets(self, node: Any) -> tuple[str, ...]:
        """沿当前 RHS 上溯到最近赋值，避免污染同一声明的兄弟变量。"""
        current = node
        while current.parent is not None:
            parent = current.parent
            if parent.type in {"variable_declarator", "assignment_expression"}:
                value = field(parent, "value") or field(parent, "right")
                if (
                    value is not None
                    and value.start_byte <= node.start_byte < value.end_byte
                ):
                    result = resolve_javascript_binding(
                        field(parent, "name") or field(parent, "left"),
                        value,
                        self.source,
                        self.path,
                    )
                    origin = self._location(node).location_id
                    return tuple(
                        item.output
                        for item in result.projections
                        if origin in item.origins
                    )
                return ()
            if parent.type in _FUNCTIONS or parent.type in {
                "expression_statement",
                "return_statement",
                "program",
            }:
                break
            current = parent
        return ()

    def _expression(self, node: Any) -> IRExpression:
        """保留参数文本、字面量、词法变量和回调位置名称，不推断动态值。"""
        if node is None:
            return IRExpression("")
        raw = self._text(node)
        literal: Any = None
        is_literal = node.type in {"string", "number", "true", "false", "null"}
        if is_literal:
            try:
                literal = (
                    {"true": True, "false": False, "null": None}[raw]
                    if raw in {"true", "false", "null"}
                    else ast.literal_eval(raw)
                )
            except (ValueError, SyntaxError):
                literal = raw
        parts = (
            javascript_function(node, self.source) if node.type in _FUNCTIONS else None
        )
        identifiers = (parts[0],) if parts else self._identifiers(node)
        return IRExpression(
            raw[:1000],
            identifiers,
            is_literal,
            literal,
            node.type,
            any(item.type in _CALLS for item in self._walk(node)),
            javascript_value(node, self.source, self.path),
        )

    def _identifiers(self, node: Any) -> tuple[str, ...]:
        """保留词法值的属性/常量下标身份，排除被调用名称和嵌套函数。"""
        if node is None:
            return ()
        names: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type in _FUNCTIONS:
                continue
            access = javascript_access_name(current, self.source)
            if access:
                names.append(access)
                continue
            if current.type in _CALLS:
                callee = field(current, "function") or field(current, "constructor")
                args = field(current, "arguments")
                receiver = field(callee, "object")
                if receiver is not None:
                    pending.append(receiver)
                if args is not None:
                    pending.extend(reversed(args.named_children))
                continue
            if current.type == "shorthand_property_identifier":
                names.append(self._text(current))
            pending.extend(reversed(current.named_children))
        return tuple(dict.fromkeys(names))

    def _declared(self, root: Any, *, descend_functions: bool) -> set[str]:
        """收集作用域内声明的绑定名称，供保守的 API 遮蔽过滤使用。"""
        return {
            name
            for item in self._walk(root, skip_functions=not descend_functions)
            if item.type
            in {"variable_declarator", "function_declaration", "class_declaration"}
            for name in (
                projection.output
                for projection in StructuredBindingResolver()
                .resolve(
                    javascript_pattern(field(item, "name"), self.source, self.path),
                    BindingValue(),
                )
                .projections
            )
        }

    def _written(self, root: Any) -> set[str]:
        """重绑定的平台/导入名称失去 API 可信匹配；不把对象属性写入等同变量重绑定。"""
        result: set[str] = set()
        for item in self._walk(root, skip_functions=True):
            left = field(item, "left")
            if (
                item.type
                in {"assignment_expression", "augmented_assignment_expression"}
                and left is not None
                and left.type == "identifier"
            ):
                result.add(self._text(left))
        return result

    def _location(self, node: Any) -> IRLocation:
        """生成和公共程序图一致的 1-based 源码位置。"""
        line, column = node.start_point[0] + 1, node.start_point[1] + 1
        end_line, end_column = node.end_point[0] + 1, node.end_point[1] + 1
        return IRLocation(
            self.path,
            line,
            column,
            end_line,
            end_column,
            ProgramIdentity.location_id(self.path, line, column, end_line, end_column),
        )

    def _failure(self, node: Any, reason: str) -> None:
        """将未建模语义显式写入覆盖诊断，不能用无告警推断项目安全。"""
        self.program.failures.append(
            {
                "path": self.path,
                "language": self.program.language,
                "line": node.start_point[0] + 1,
                "reason": reason,
            }
        )

    @staticmethod
    def _walk(root: Any, *, skip_functions: bool = False) -> Iterator[Any]:
        """按源码顺序迭代 AST，按需停止进入嵌套函数体。"""
        pending = [root]
        while pending:
            node = pending.pop()
            yield node
            if skip_functions and node is not root and node.type in _FUNCTIONS:
                continue
            pending.extend(reversed(node.named_children))

    def _text(self, node: Any) -> str:
        """容错读取当前文件语法文本。"""
        return text(node, self.source)
