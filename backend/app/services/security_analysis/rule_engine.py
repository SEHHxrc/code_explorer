"""在通用 SecurityProgramIR 上执行已注册的跨语言规则包。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    DataFlowEvidence,
    EvidenceLocation,
    FactKind,
    SecurityEntrypoint,
    Severity,
    StaticSecurityFact,
    TrustClass,
)
from .ir import (
    IRCall,
    IRDecorator,
    IRExpression,
    IRFunction,
    IRLocation,
    IRValueBoundary,
    SecurityProgramIR,
)
from .rules import CallCondition, CallRule, EntrypointRule, RulePack


@dataclass
class SecurityScanResult:
    """通用规则引擎产生的安全事实、覆盖率和规则包信息。"""

    entrypoints: list[SecurityEntrypoint] = field(default_factory=list)
    sources: list[StaticSecurityFact] = field(default_factory=list)
    sinks: list[StaticSecurityFact] = field(default_factory=list)
    guards: list[StaticSecurityFact] = field(default_factory=list)
    sanitizers: list[StaticSecurityFact] = field(default_factory=list)
    files_considered: int = 0
    files_scanned: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    languages_analyzed: list[str] = field(default_factory=list)
    unsupported_languages: list[str] = field(default_factory=list)
    rule_packs: list[str] = field(default_factory=list)
    dataflows: list[DataFlowEvidence] = field(default_factory=list)
    value_boundaries: list[IRValueBoundary] = field(default_factory=list)

    def merge(self, other: SecurityScanResult) -> None:
        """合并另一语言扫描结果并保持列表去重。"""
        self.entrypoints.extend(other.entrypoints)
        self.sources.extend(other.sources)
        self.sinks.extend(other.sinks)
        self.guards.extend(other.guards)
        self.sanitizers.extend(other.sanitizers)
        self.value_boundaries.extend(other.value_boundaries)
        self.files_considered += other.files_considered
        self.files_scanned += other.files_scanned
        self.failures.extend(other.failures)
        self.languages_analyzed = sorted(set(self.languages_analyzed + other.languages_analyzed))
        self.unsupported_languages = sorted(
            set(self.unsupported_languages + other.unsupported_languages)
        )
        self.rule_packs = sorted(set(self.rule_packs + other.rule_packs))
        self.dataflows.extend(other.dataflows)


PythonScanResult = SecurityScanResult


class SecurityRuleEngine:
    """不依赖具体语言 AST，只消费通用 IR 和规则包。"""

    def evaluate(
        self,
        program: SecurityProgramIR,
        rule_packs: tuple[RulePack, ...],
    ) -> SecurityScanResult:
        """执行适用于 IR 语言的入口、调用和访问规则。"""
        applicable = tuple(
            pack for pack in rule_packs if program.language in pack.languages
        )
        result = SecurityScanResult(
            files_considered=program.files_considered,
            files_scanned=program.files_scanned,
            failures=list(program.failures),
            languages_analyzed=[program.language],
            rule_packs=[pack.identifier for pack in applicable],
            value_boundaries=list(program.value_boundaries),
        )
        fact_ids: set[str] = set()
        for pack in applicable:
            for rule in pack.entrypoint_rules:
                self._evaluate_entrypoints(program, rule, result, fact_ids)
        call_rules = tuple(rule for pack in applicable for rule in pack.call_rules)
        access_rules = tuple(rule for pack in applicable for rule in pack.access_rules)
        unresolved_condition_calls: set[str] = set()
        for call in program.calls:
            for rule in call_rules:
                if program.language not in rule.languages:
                    continue
                if not rule.matches_name(call.qualified_name):
                    continue
                if rule.required_headers:
                    if call.callee_is_project_defined:
                        continue
                    if not set(rule.required_headers).intersection(call.visible_headers):
                        result.failures.append({
                            "path": call.location.path,
                            "line": call.location.line,
                            "callsite_id": call.callsite_id,
                            "reason": "api_header_not_visible",
                            "rule_id": rule.rule_id,
                        })
                        continue
                condition_match = self._conditions_match(call, rule.conditions)
                if condition_match is None:
                    if call.callsite_id not in unresolved_condition_calls:
                        unresolved_condition_calls.add(call.callsite_id)
                        result.failures.append({
                            "path": call.location.path,
                            "line": call.location.line,
                            "callsite_id": call.callsite_id,
                            "reason": "rule_condition_unresolved",
                        })
                    continue
                if not condition_match:
                    continue
                if rule.output_argument_positions and not all(0 <= index < len(call.arguments) and call.arguments[index].kind in {"identifier", "name"} and len(call.arguments[index].identifiers) == 1 for index in rule.output_argument_positions):
                    result.failures.append({
                        "path": call.location.path,
                        "line": call.location.line,
                        "callsite_id": call.callsite_id,
                        "reason": "output_argument_binding_unresolved",
                        "rule_id": rule.rule_id,
                    })
                self._append_fact(
                    result,
                    fact_ids,
                    rule.fact_kind,
                    rule.rule_id,
                    rule.category,
                    call.qualified_name,
                    call.symbol,
                    call.location,
                    cwe=rule.cwe,
                    trust_class=rule.trust_class,
                    severity=rule.severity,
                    metadata=self._call_metadata(call, rule),
                )
        for access in program.accesses:
            for rule in access_rules:
                if program.language not in rule.languages:
                    continue
                if access.qualified_name not in rule.names or access.access_kind not in rule.access_kinds:
                    continue
                self._append_fact(
                    result,
                    fact_ids,
                    rule.fact_kind,
                    rule.rule_id,
                    rule.category,
                    access.qualified_name,
                    access.symbol,
                    access.location,
                    cwe=rule.cwe,
                    trust_class=rule.trust_class,
                    severity=rule.severity,
                    metadata={
                        "assigned_targets": list(access.assigned_targets),
                        "value_identifiers": list(access.value_identifiers),
                        "parameter_seed": access.parameter_seed,
                        **({"binding_certainty": "may"} if access.binding_certainty == "may" else {}),
                        "preconditions": list(rule.preconditions),
                        "value_flow": {
                            "result": "parameter" if access.parameter_seed else "",
                            "result_role": rule.result_role,
                            "argument_role": rule.argument_role,
                        },
                    },
                )
        for condition in program.conditions:
            prefix = program.language.upper().replace("-", "_")
            self._append_fact(
                result,
                fact_ids,
                "guard",
                f"{prefix}-GUARD-ASSERT" if condition.kind == "assert"
                else f"{prefix}-GUARD-CONDITION",
                "assertion" if condition.kind == "assert" else "conditional_check",
                condition.expression.text[:300] or condition.kind,
                condition.symbol,
                condition.location,
                metadata={"identifiers": list(condition.expression.identifiers)},
            )
        return result

    def _evaluate_entrypoints(
        self,
        program: SecurityProgramIR,
        rule: EntrypointRule,
        result: SecurityScanResult,
        fact_ids: set[str],
    ) -> None:
        """执行一个框架入口点规则。"""
        if program.language not in rule.languages:
            return
        instances = {
            target
            for call in program.calls
            if call.qualified_name in rule.factory_names
            for target in call.assigned_targets
        }
        for function in program.functions:
            route = self._decorated_entrypoint(function.decorators, instances, rule)
            if route is None:
                route = self._owned_method_entrypoint(
                    function.name,
                    function.owner_types,
                    rule,
                )
            if route is None:
                route = self._registered_function_entrypoint(
                    function,
                    program.calls,
                    rule,
                )
            if route is None:
                continue
            method, route_path = route
            self._append_entrypoint(result, function.symbol, function.location, method, route_path, rule)
            for position, parameter in enumerate(function.parameters):
                if rule.source_parameter_positions is not None and position not in rule.source_parameter_positions:
                    continue
                if parameter.name in {"self", "cls"}:
                    continue
                if any(
                    fragment in parameter.annotation
                    for fragment in rule.excluded_annotation_fragments
                ):
                    continue
                if any(parameter.default_call.endswith(suffix) for suffix in rule.dependency_suffixes):
                    self._append_fact(
                        result, fact_ids, "guard", f"{rule.rule_id}-DEPENDENCY-GUARD",
                        "framework_dependency", f"Depends({parameter.name})",
                        function.symbol, parameter.location,
                        trust_class="framework_dependency",
                        metadata={"parameter": parameter.name, "framework": rule.framework},
                    )
                    continue
                category = rule.default_parameter_category
                for annotation, matched_category in rule.annotation_categories:
                    if annotation in parameter.annotation:
                        category = matched_category
                        break
                if method in rule.websocket_methods and category == rule.default_parameter_category:
                    category = "websocket_input"
                self._append_fact(
                    result, fact_ids, "source", f"{rule.rule_id}-PARAM", category,
                    parameter.name, function.symbol, parameter.location,
                    trust_class="untrusted",
                    metadata={
                        "framework": rule.framework,
                        "route_method": method,
                        "route_path": route_path,
                        "annotation": parameter.annotation,
                        "value_flow": {"result": "parameter"},
                    },
                )

    @staticmethod
    def _decorated_entrypoint(
        decorators: tuple[IRDecorator, ...],
        instances: set[str],
        rule: EntrypointRule,
    ) -> tuple[str, str] | None:
        """匹配工厂实例上的装饰器入口。"""
        for decorator in decorators:
            direct_method = next((
                method
                for annotation, method in rule.direct_decorator_methods
                if decorator.qualified_name == annotation
                or decorator.qualified_name.endswith(f".{annotation}")
            ), None)
            if direct_method is not None:
                route_path = "<dynamic>"
                if decorator.arguments and decorator.arguments[0].is_literal:
                    route_path = str(decorator.arguments[0].literal)
                return direct_method, route_path
            if "." not in decorator.qualified_name:
                continue
            receiver, method = decorator.qualified_name.rsplit(".", 1)
            if method not in rule.decorator_methods:
                continue
            if receiver not in instances and receiver.split(".", 1)[0] not in instances:
                continue
            route_path = "<dynamic>"
            if decorator.arguments and decorator.arguments[0].is_literal:
                route_path = str(decorator.arguments[0].literal)
            return method, route_path
        return None

    @staticmethod
    def _owned_method_entrypoint(
        function_name: str,
        owner_types: tuple[str, ...],
        rule: EntrypointRule,
    ) -> tuple[str, str] | None:
        """匹配由已观察继承类型约束的方法式入口，例如 Servlet。"""
        method = next((
            protocol_method
            for name, protocol_method in rule.owner_method_mappings
            if name == function_name
        ), None)
        if method is None:
            return None
        if not any(
            owner == suffix or owner.endswith(f".{suffix}")
            for owner in owner_types
            for suffix in rule.owner_type_suffixes
        ):
            return None
        return method, "<container-mapped>"

    @staticmethod
    def _registered_function_entrypoint(
        function: IRFunction,
        calls: list[IRCall],
        rule: EntrypointRule,
    ) -> tuple[str, str] | None:
        """匹配 ``register(path, handler)`` 形式的显式函数注册入口。"""
        if not rule.registration_call_methods:
            return None
        for call in calls:
            method = next((
                http_method
                for name, http_method in rule.registration_call_methods
                if call.qualified_name == name or call.qualified_name.endswith(f".{name}")
            ), None)
            if method is None:
                continue
            handler_position = rule.registration_handler_position
            if handler_position < 0:
                handler_position += len(call.arguments)
            if handler_position < 0 or handler_position >= len(call.arguments):
                continue
            handler = call.arguments[handler_position]
            handler_names = set(handler.identifiers) | {handler.text}
            if not any(
                name == function.name
                or name.endswith(f".{function.name}")
                or name.endswith(f"::{function.name}")
                for name in handler_names
            ):
                continue
            route_path = "<dynamic>"
            path_position = rule.registration_path_position
            if 0 <= path_position < len(call.arguments):
                route = call.arguments[path_position]
                if route.is_literal:
                    route_path = str(route.literal)
            return method, route_path
        return None

    @staticmethod
    def _append_entrypoint(
        result: SecurityScanResult,
        symbol: str,
        location: IRLocation,
        method: str,
        route_path: str,
        rule: EntrypointRule,
    ) -> None:
        """记录入口点，但不把入口控制可达性伪装成不可信数据。"""
        material = "\x1f".join((symbol, rule.rule_id, method, route_path, str(location.line)))
        result.entrypoints.append(SecurityEntrypoint(
            entrypoint_id="entrypoint:" + hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()[:24],
            kind="websocket" if method in rule.websocket_methods else "http_route",
            framework=rule.framework,
            symbol=symbol,
            location=SecurityRuleEngine._evidence_location(location),
            metadata={
                "entrypoint_rule_id": rule.rule_id,
                "route_method": method,
                "route_path": route_path,
            },
        ))

    @classmethod
    def _conditions_match(
        cls,
        call: IRCall,
        conditions: tuple[CallCondition, ...],
    ) -> bool | None:
        """匹配声明式条件；无法静态求值时返回 ``None``。"""
        for condition in conditions:
            expression: IRExpression | None = None
            if condition.argument_position is not None and len(call.arguments) > condition.argument_position:
                expression = call.arguments[condition.argument_position]
            if expression is None and condition.keyword:
                expression = call.keyword(condition.keyword)
            if expression is None:
                if condition.keyword and call.unknown_keywords:
                    return None
                if not condition.has_default:
                    return False
                value = condition.default
            elif not expression.is_literal:
                return None
            else:
                value = expression.literal
            if condition.operator == "equals" and value not in condition.values:
                return False
            if condition.operator == "contains_any" and (
                not isinstance(value, str) or not any(str(item) in value for item in condition.values)
            ):
                return False
            if condition.operator == "excludes_any" and (
                not isinstance(value, str) or any(str(item) in value for item in condition.values)
            ):
                return False
        return True

    @classmethod
    def _append_fact(
        cls,
        result: SecurityScanResult,
        fact_ids: set[str],
        fact_kind: FactKind,
        rule_id: str,
        category: str,
        name: str,
        symbol: str,
        location: IRLocation,
        *,
        cwe: str | None = None,
        trust_class: TrustClass = "unknown",
        severity: Severity = "informational",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """构造、去重并保存一条静态安全事实。"""
        material = "\x1f".join((
            location.location_id, fact_kind, rule_id, symbol, name,
        ))
        fact_id = "fact:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        if fact_id in fact_ids:
            return
        fact_ids.add(fact_id)
        fact = StaticSecurityFact(
            fact_id=fact_id,
            fact_kind=fact_kind,
            rule_id=rule_id,
            category=category,
            name=name or rule_id,
            symbol=symbol,
            location=cls._evidence_location(location),
            cwe=cwe,
            trust_class=trust_class,
            severity=severity,
            metadata=metadata or {},
        )
        collection = {
            "source": result.sources,
            "sink": result.sinks,
            "guard": result.guards,
            "sanitizer": result.sanitizers,
        }[fact_kind]
        collection.append(fact)

    @staticmethod
    def _evidence_location(location: IRLocation) -> EvidenceLocation:
        """把内部 IR 位置转换为持久化证据位置。"""
        return EvidenceLocation(
            path=location.path,
            line=location.line,
            column=location.column,
            end_line=location.end_line,
            end_column=location.end_column,
            location_id=location.location_id,
        )

    @staticmethod
    def _call_metadata(call: IRCall, rule: CallRule) -> dict[str, Any]:
        """保存调用点、参数角色及少量跨语言通用参数形态。"""
        metadata: dict[str, Any] = {
            "callsite_id": call.callsite_id,
            "argument_count": len(call.arguments),
            "assigned_targets": list(call.assigned_targets),
            "value_flow": {
                "result_role": rule.result_role,
                "argument_role": rule.argument_role,
                "arguments": list(rule.argument_positions),
                "keywords": list(rule.argument_keywords),
                "receiver_role": rule.receiver_role,
                "output_arguments": list(rule.output_argument_positions),
            },
        }
        if rule.required_headers:
            metadata["api_resolution"] = "header_and_lexical_scope"
            metadata["rule_match_confidence"] = "medium"
            metadata["limitations"] = [
                "库 API 匹配依据可见头文件与词法名称，尚未由编译器类型绑定或预处理结果验证。"
            ]
            metadata["visible_headers"] = sorted(
                set(rule.required_headers).intersection(call.visible_headers)
            )
        if rule.preconditions:
            metadata["preconditions"] = list(rule.preconditions)
        if rule.output_argument_positions:
            metadata["binding_certainty"] = "may"
            metadata["value_flow"]["output_targets"] = {str(index): call.arguments[index].identifiers[0] for index in rule.output_argument_positions if 0 <= index < len(call.arguments) and call.arguments[index].kind in {"identifier", "name"} and len(call.arguments[index].identifiers) == 1}
            metadata.setdefault("limitations", []).append(
                "输出参数只支持独立调用的具名缓冲区 may 写入；返回状态码不是输入内容，复杂指针、同操作多调用、字节范围及成功条件未建模。"
            )
        if rule.result_positions:
            metadata["value_flow"]["result_positions"] = list(rule.result_positions)
        if rule.result_count > 1:
            metadata["value_flow"]["result_count"] = rule.result_count
        if rule.category in {"process_execution", "shell_execution"}:
            shell = call.keyword("shell")
            metadata["shell"] = rule.implicit_shell if rule.implicit_shell is not None else (
                shell.literal if shell and shell.is_literal else None
            )
        if rule.category == "sql_execution" and call.arguments:
            position = rule.argument_positions[0] if rule.argument_positions else 0
            first = call.arguments[position] if position < len(call.arguments) else None
            metadata["query_shape"] = (
                "literal" if first and first.is_literal and isinstance(first.literal, str)
                else "formatted" if first and first.text.startswith("f")
                else "dynamic"
            )
            if rule.sql_parameter_argument_position is not None:
                metadata["parameter_argument_present"] = (
                    len(call.arguments) > rule.sql_parameter_argument_position or bool(call.keywords)
                )
        if rule.category in {"file_read", "file_write"} and rule.conditions:
            mode = call.keyword("mode")
            if mode is None and len(call.arguments) > 1:
                mode = call.arguments[1]
            metadata["mode"] = mode.literal if mode and mode.is_literal else "r"
        return metadata


PythonSecurityRuleEngine = SecurityRuleEngine
