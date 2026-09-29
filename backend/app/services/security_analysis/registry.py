"""语言前端、语言语义和规则包的显式注册表。"""

from __future__ import annotations

from .frontends import LanguageFrontend
from .rules import RulePack
from .semantics import LanguageSemantics


class FrontendRegistry:
    """按语言唯一注册安全分析前端。"""

    def __init__(self, frontends: list[LanguageFrontend] | None = None) -> None:
        """输入初始前端并拒绝语言重复。"""
        self._frontends: dict[str, LanguageFrontend] = {}
        for frontend in frontends or []:
            self.register(frontend)

    def register(self, frontend: LanguageFrontend) -> None:
        """注册一个语言前端；重复语言会显式失败。"""
        if frontend.language in self._frontends:
            raise ValueError(f"Duplicate security frontend: {frontend.language}")
        self._frontends[frontend.language] = frontend

    def get(self, language: str) -> LanguageFrontend | None:
        """返回指定语言前端，不支持时返回 ``None``。"""
        return self._frontends.get(language)

    def languages(self) -> tuple[str, ...]:
        """返回稳定排序的已注册语言。"""
        return tuple(sorted(self._frontends))


class SemanticsRegistry:
    """按语言唯一注册调用边界和未来数据流语义。"""

    def __init__(self, semantics: list[LanguageSemantics] | None = None) -> None:
        """输入初始语言语义。"""
        self._semantics: dict[str, LanguageSemantics] = {}
        for item in semantics or []:
            self.register(item)

    def register(self, semantics: LanguageSemantics) -> None:
        """注册语言语义；重复语言会显式失败。"""
        if semantics.language in self._semantics:
            raise ValueError(f"Duplicate language semantics: {semantics.language}")
        self._semantics[semantics.language] = semantics

    def get(self, language: str) -> LanguageSemantics | None:
        """返回指定语言语义。"""
        return self._semantics.get(language)


class RulePackRegistry:
    """按标识去重注册规则包，并按语言选择适用规则。"""

    def __init__(self, packs: list[RulePack] | tuple[RulePack, ...] | None = None) -> None:
        """输入初始规则包。"""
        self._packs: dict[str, RulePack] = {}
        for pack in packs or ():
            self.register(pack)

    def register(self, pack: RulePack) -> None:
        """注册规则包；相同名称和版本不得静默覆盖。"""
        if pack.identifier in self._packs:
            raise ValueError(f"Duplicate security rule pack: {pack.identifier}")
        new_rule_ids = self._rule_ids(pack)
        for existing in self._packs.values():
            if not set(pack.languages).intersection(existing.languages):
                continue
            duplicates = new_rule_ids.intersection(self._rule_ids(existing))
            if duplicates:
                raise ValueError(
                    "Duplicate security rule ids for the same language: "
                    + ", ".join(sorted(duplicates))
                )
        self._packs[pack.identifier] = pack

    def for_language(self, language: str) -> tuple[RulePack, ...]:
        """返回适用于指定语言的稳定排序规则包。"""
        return tuple(
            self._packs[key]
            for key in sorted(self._packs)
            if language in self._packs[key].languages
        )

    def identifiers(self) -> tuple[str, ...]:
        """返回全部已注册规则包标识。"""
        return tuple(sorted(self._packs))

    @staticmethod
    def _rule_ids(pack: RulePack) -> set[str]:
        """返回一个规则包声明的全部逻辑规则标识。"""
        return {
            rule.rule_id
            for rules in (pack.call_rules, pack.access_rules, pack.entrypoint_rules)
            for rule in rules
        }
