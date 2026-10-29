"""按依赖分析语言范围编排多语言安全前端和规则包。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.services.semantic_index import CallTransitionIndex, SemanticIndexArtifact
from backend.app.services.semantic_index.projection import ProgramFacts

from .language_adapters import (
    CSecurityFrontend,
    CppSecurityFrontend,
    GoSecurityFrontend,
    JavaSecurityFrontend,
    JavaScriptSecurityFrontend,
    TypeScriptSecurityFrontend,
    PythonSecurityFrontend,
)
from .registry import FrontendRegistry, RulePackRegistry, SemanticsRegistry
from .rule_engine import SecurityRuleEngine, SecurityScanResult
from .parameter_apis import forward_parameter_apis
from .rules import C_FAMILY_RULE_PACKS, GO_RULE_PACKS, JAVA_RULE_PACKS, PYTHON_RULE_PACKS, JAVASCRIPT_RULE_PACKS
from .semantics import (
    CLanguageSemantics,
    CppLanguageSemantics,
    GoLanguageSemantics,
    JavaLanguageSemantics,
    JavaScriptLanguageSemantics,
    TypeScriptLanguageSemantics,
    PythonLanguageSemantics,
)


class SecurityScanner:
    """对已注册语言执行统一 IR、规则和覆盖率编排。"""

    def __init__(
        self,
        *,
        frontends: FrontendRegistry | None = None,
        rule_packs: RulePackRegistry | None = None,
        semantics: SemanticsRegistry | None = None,
        rule_engine: SecurityRuleEngine | None = None,
    ) -> None:
        """注入扩展注册表；默认启用 Python、Java、Go、C/C++、JS/TS 安全前端。"""
        self.frontends = frontends or FrontendRegistry([
            PythonSecurityFrontend(),
            JavaSecurityFrontend(),
            GoSecurityFrontend(),
            CSecurityFrontend(),
            CppSecurityFrontend(),
            JavaScriptSecurityFrontend(),
            TypeScriptSecurityFrontend(),
        ])
        self.rule_packs = rule_packs or RulePackRegistry(
            PYTHON_RULE_PACKS + JAVA_RULE_PACKS + GO_RULE_PACKS + C_FAMILY_RULE_PACKS + JAVASCRIPT_RULE_PACKS
        )
        self.semantics = semantics or SemanticsRegistry([
            PythonLanguageSemantics(),
            JavaLanguageSemantics(),
            GoLanguageSemantics(),
            CLanguageSemantics(),
            CppLanguageSemantics(),
            JavaScriptLanguageSemantics(),
            TypeScriptLanguageSemantics(),
        ])
        self.rule_engine = rule_engine or SecurityRuleEngine()

    def scan(
        self,
        project_root: str | Path,
        *,
        dependency_graph: dict[str, Any] | None = None,
        semantic_index: SemanticIndexArtifact | dict[str, Any] | None = None,
    ) -> SecurityScanResult:
        """按依赖图语言分组扫描；不支持的语言显式进入诊断。"""
        aggregate = SecurityScanResult()
        files_by_language = ProgramFacts(semantic_index, dependency_graph).files_by_language()
        if files_by_language is None:
            for language in self.frontends.languages():
                frontend = self.frontends.get(language)
                if frontend is None:
                    continue
                program = frontend.build(project_root)
                result = self.rule_engine.evaluate(
                    program,
                    self.rule_packs.for_language(language),
                )
                aggregate.merge(result)
            return aggregate

        transitions: CallTransitionIndex | None = None
        for language in sorted(files_by_language):
            paths = files_by_language[language]
            frontend = self.frontends.get(language)
            if frontend is None:
                aggregate.files_considered += len(paths)
                aggregate.unsupported_languages.append(language)
                aggregate.failures.append({
                    "reason": "unsupported_language",
                    "language": language,
                    "file_count": len(paths),
                })
                continue
            program = frontend.build(project_root, file_paths=paths)
            semantics = self.semantics.get(language)
            packs = self.rule_packs.for_language(language)
            if semantics is not None and any(parameter.api_type for function in program.functions for parameter in function.parameters):
                if transitions is None:
                    transitions = CallTransitionIndex.from_sources(semantic_index=semantic_index, dependency_graph=dependency_graph)
                forward_parameter_apis(program, transitions, semantics,
                                       {name for pack in packs for rule in pack.access_rules if rule.fact_kind == "source" for name in rule.names})
            result = self.rule_engine.evaluate(
                program,
                packs,
            )
            aggregate.merge(result)
        aggregate.unsupported_languages = sorted(set(aggregate.unsupported_languages))
        return aggregate
