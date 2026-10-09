"""公共绑定 IR：各语言适配语法，下游只消费变量和字段/位置选择关系。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class BindingValue:
    """一个原始实参/初始化值；opaque 表示结构未知，missing 与 literal 必须区别。"""

    kind: Literal["opaque", "object", "array", "literal", "missing"] = "opaque"
    variables: tuple[str, ...] = ()
    members: tuple[ValueMember, ...] = ()
    origins: tuple[str, ...] = ()
    uncertain: bool = False
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValueMember:
    """可由语法观察到的对象字段或数组位置及其值。"""

    selector: str | int
    value: BindingValue


@dataclass(frozen=True)
class BindingPattern:
    """不含语言 AST 的绑定模式：名称、嵌套对象/数组、默认值、rest 与未知模式。"""

    kind: Literal["name", "object", "array", "default", "rest", "unknown"]
    name: str = ""
    members: tuple[PatternMember, ...] = ()
    fallback: BindingValue | None = None
    reason: str = ""


@dataclass(frozen=True)
class PatternMember:
    """模式成员；None 只表示未知选择器，数组空位由适配器保留位置后不生成成员。"""

    selector: str | int | None
    pattern: BindingPattern


@dataclass(frozen=True)
class BindingProjection:
    """一项叶绑定输出及其输入身份、源码起点和不确定性。"""

    output: str
    inputs: tuple[str, ...] = ()
    origins: tuple[str, ...] = ()
    certainty: Literal["must", "may"] = "must"


@dataclass(frozen=True)
class BindingResolution:
    """同一次模式展开的结果与未建模原因；空输入的字面量绑定不能被丢弃。"""

    projections: tuple[BindingProjection, ...] = ()
    diagnostics: tuple[str, ...] = ()


class PatternResolver(Protocol):
    """任意语言生成同一绑定 IR 后，使用同一投影算法。"""

    def resolve(
        self, pattern: BindingPattern, value: BindingValue
    ) -> BindingResolution:
        """输入绑定模式和值，输出有界的叶绑定与诊断，不判断输入是否受污染。"""
        ...
