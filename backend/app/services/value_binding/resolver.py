"""公共对象/序列投影算法，负责默认值与 rest，不解释语言语法。"""

from __future__ import annotations

import json

from .contracts import (
    BindingPattern,
    BindingProjection,
    BindingResolution,
    BindingValue,
    ValueMember,
)


def append_selector(variable: str, selector: str | int) -> str:
    """生成与词法访问路径一致的字段/位置身份；只有标识符字段使用点号。"""
    if isinstance(selector, str) and selector.isidentifier():
        return f"{variable}.{selector}"
    return f"{variable}[{json.dumps(selector, ensure_ascii=False)}]"


class StructuredBindingResolver:
    """有限深度/数量的模式投影；未知选择器诊断后不伪造确定字段。"""

    def __init__(self, *, max_depth: int = 24, max_projections: int = 512) -> None:
        """输入递归与输出上限，防止恶意模式/对象使分析膨胀。"""
        self.max_depth = max_depth
        self.max_projections = max_projections

    def resolve(
        self, pattern: BindingPattern, value: BindingValue
    ) -> BindingResolution:
        """返回精确叶绑定，包括重命名、嵌套选择、默认分支、静态 rest 与字面量覆写。"""
        projections: list[BindingProjection] = []
        diagnostics: list[str] = []

        def emit(name: str, current: BindingValue, depth: int) -> None:
            """记录绑定及已知子字段；空输入显式保留为固定值定义。"""
            if not name:
                return
            diagnostics.extend(current.diagnostics)
            if len(projections) >= self.max_projections or depth > self.max_depth:
                diagnostics.append("binding_projection_budget_exceeded")
                return
            projections.append(
                BindingProjection(
                    name,
                    current.variables,
                    current.origins,
                    "may" if current.uncertain else "must",
                )
            )
            if current.uncertain and current.kind in {"object", "array"}:
                diagnostics.append("structured_value_contains_unknown_members")
            for member in current.members:
                emit(append_selector(name, member.selector), member.value, depth + 1)

        def visit(target: BindingPattern, current: BindingValue, depth: int) -> None:
            """递归投影一种公共模式，未知语义只建立 may 关系或显式诊断。"""
            diagnostics.extend(current.diagnostics)
            if depth > self.max_depth or len(projections) >= self.max_projections:
                diagnostics.append("binding_projection_budget_exceeded")
                return
            if target.kind == "name":
                emit(target.name, current, depth)
            elif target.kind == "unknown":
                diagnostics.append(target.reason or "unknown_binding_pattern")
            elif target.kind in {"default", "rest"}:
                if not target.members:
                    return
                if target.kind == "default":
                    fallback = target.fallback or BindingValue(kind="literal")
                    if current.kind == "missing":
                        current = fallback
                    elif current.kind == "opaque":
                        current = BindingValue(
                            variables=tuple(
                                dict.fromkeys((*current.variables, *fallback.variables))
                            ),
                            origins=tuple(
                                dict.fromkeys((*current.origins, *fallback.origins))
                            ),
                            uncertain=True,
                        )
                        diagnostics.append("default_presence_not_proven")
                visit(target.members[0].pattern, current, depth + 1)
            else:
                known = {member.selector: member.value for member in current.members}
                selected: set[str | int] = set()
                if current.uncertain and current.kind in {"object", "array"}:
                    diagnostics.append("structured_value_contains_unknown_members")
                dynamic = False
                for member in target.members:
                    if member.pattern.kind == "rest":
                        if (
                            current.kind in {"array", "object"}
                            and not dynamic
                            and not current.uncertain
                        ):
                            remaining = [
                                item
                                for item in current.members
                                if item.selector not in selected
                            ]
                            if target.kind == "array" and isinstance(
                                member.selector, int
                            ):
                                remaining = [
                                    ValueMember(
                                        int(item.selector) - member.selector, item.value
                                    )
                                    for item in remaining
                                    if isinstance(item.selector, int)
                                    and item.selector >= member.selector
                                ]
                            rest = BindingValue(
                                kind=current.kind,
                                members=tuple(remaining),
                                variables=tuple(
                                    dict.fromkeys(
                                        name
                                        for item in remaining
                                        for name in item.value.variables
                                    )
                                ),
                                origins=tuple(
                                    dict.fromkeys(
                                        origin
                                        for item in remaining
                                        for origin in item.value.origins
                                    )
                                ),
                                uncertain=current.uncertain,
                            )
                        else:
                            diagnostics.append("opaque_rest_exclusions_not_proven")
                            rest = BindingValue(
                                variables=current.variables,
                                origins=current.origins,
                                uncertain=True,
                            )
                        visit(member.pattern, rest, depth + 1)
                        continue
                    if member.selector is None:
                        dynamic = True
                        diagnostics.append("dynamic_binding_selector_not_resolved")
                        continue
                    selected.add(member.selector)
                    if current.kind in {"object", "array"}:
                        chosen = known.get(
                            member.selector,
                            BindingValue(
                                variables=current.variables,
                                origins=current.origins,
                                uncertain=True,
                            )
                            if current.uncertain
                            else BindingValue(kind="missing"),
                        )
                    elif current.kind == "opaque":
                        chosen = BindingValue(
                            variables=tuple(
                                append_selector(name, member.selector)
                                for name in current.variables
                            ),
                            origins=current.origins,
                            uncertain=True,
                        )
                    else:
                        chosen = BindingValue(kind="missing")
                    visit(member.pattern, chosen, depth + 1)

        visit(pattern, value, 0)
        return BindingResolution(
            tuple(dict.fromkeys(projections)), tuple(dict.fromkeys(diagnostics))
        )
