"""跨语言安全规则包的公共声明契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..contracts import FactKind, Severity, TrustClass


@dataclass(frozen=True)
class CallCondition:
    """对静态调用参数施加的声明式匹配条件。"""

    operator: Literal["contains_any", "excludes_any", "equals"]
    values: tuple[Any, ...]
    argument_position: int | None = None
    keyword: str = ""
    has_default: bool = False
    default: Any = None


@dataclass(frozen=True)
class CallRule:
    """匹配规范化调用名称，并声明值在调用边界上的安全角色。

    output_argument_positions 表示调用写入的零起始实参位置，不是返回值。
    result_positions/result_count 表示多返回 API 的内容分量和数量；缺省为单返回。
    这些字段只提供规则角色，由公共消费者建立 may 值流，不证明调用成功或漏洞可利用。
    """

    rule_id: str
    fact_kind: FactKind
    category: str
    names: tuple[str, ...]
    languages: tuple[str, ...] = ("python",)
    cwe: str | None = None
    trust_class: TrustClass = "unknown"
    severity: Severity = "informational"
    match_suffix: bool = False
    result_role: Literal["none", "source", "sanitized"] = "none"
    argument_role: Literal["none", "sink", "guard", "sanitizer_input"] = "none"
    argument_positions: tuple[int, ...] = ()
    argument_keywords: tuple[str, ...] = ()
    receiver_role: Literal["none", "source", "sink", "sanitizer_input"] = "none"
    conditions: tuple[CallCondition, ...] = ()
    required_headers: tuple[str, ...] = ()
    output_argument_positions: tuple[int, ...] = ()
    implicit_shell: bool | None = None
    sql_parameter_argument_position: int | None = 1
    preconditions: tuple[str, ...] = ()
    result_positions: tuple[int, ...] = ()
    result_count: int = 1

    def matches_name(self, qualified_name: str) -> bool:
        """判断规范化调用名是否符合本规则。"""
        if qualified_name in self.names:
            return True
        return self.match_suffix and any(
            qualified_name.endswith(f".{name}") or qualified_name == name
            for name in self.names
        )


@dataclass(frozen=True)
class AccessRule:
    """匹配下标或属性等非调用访问。"""

    rule_id: str
    fact_kind: FactKind
    category: str
    names: tuple[str, ...]
    access_kinds: tuple[Literal["read", "write"], ...] = ("read",)
    languages: tuple[str, ...] = ("python",)
    cwe: str | None = None
    trust_class: TrustClass = "unknown"
    severity: Severity = "informational"
    result_role: Literal["none", "source"] = "none"
    argument_role: Literal["none", "sink"] = "none"
    preconditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntrypointRule:
    """声明框架工厂、直接注解或继承式入口及参数分类规则。"""

    rule_id: str
    framework: str
    languages: tuple[str, ...]
    factory_names: tuple[str, ...] = ()
    decorator_methods: tuple[str, ...] = ()
    direct_decorator_methods: tuple[tuple[str, str], ...] = ()
    owner_method_mappings: tuple[tuple[str, str], ...] = ()
    owner_type_suffixes: tuple[str, ...] = ()
    registration_call_methods: tuple[tuple[str, str], ...] = ()
    registration_path_position: int = 0
    registration_handler_position: int = 1
    dependency_suffixes: tuple[str, ...] = ()
    annotation_categories: tuple[tuple[str, str], ...] = ()
    excluded_annotation_fragments: tuple[str, ...] = ()
    default_parameter_category: str = "framework_parameter"
    websocket_methods: tuple[str, ...] = ("websocket",)
    source_parameter_positions: tuple[int, ...] | None = None


@dataclass(frozen=True)
class RulePack:
    """一个可独立版本化、按语言或框架注册的规则集合。"""

    name: str
    version: str
    languages: tuple[str, ...]
    call_rules: tuple[CallRule, ...] = ()
    access_rules: tuple[AccessRule, ...] = ()
    entrypoint_rules: tuple[EntrypointRule, ...] = ()

    @property
    def identifier(self) -> str:
        """返回持久化使用的规则包名称与版本。"""
        return f"{self.name}/{self.version}"

    def coverage_descriptor(self, language: str) -> dict[str, Any]:
        """记录本次语言实际启用的规则种类；不声称框架被使用或语义覆盖完整。"""
        calls = [rule for rule in self.call_rules if language in rule.languages]
        accesses = [rule for rule in self.access_rules if language in rule.languages]
        entries = [rule for rule in self.entrypoint_rules if language in rule.languages]
        return {
            "pack": self.identifier, "language": language,
            "entrypoint_rules": len(entries), "call_rules": len(calls), "access_rules": len(accesses),
            "source_categories": sorted({rule.category for rule in [*calls, *accesses] if rule.fact_kind == "source"}),
            "sink_categories": sorted({rule.category for rule in [*calls, *accesses] if rule.fact_kind == "sink"}),
            "framework_matchers": sorted({rule.framework for rule in entries}),
            "semantics": "enabled_matchers_not_framework_detection_or_exhaustive_coverage",
        }


class RulePackProvider(Protocol):
    """允许内置或插件规则包使用统一加载接口。"""

    def get_rule_pack(self) -> RulePack:
        """返回不可变规则包。"""
        ...
