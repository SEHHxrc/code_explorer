"""原生工具适配公共入口；只输出 semantic_index 契约，不拥有安全规则或图。"""

from collections.abc import Mapping

from backend.app.services.semantic_index import ProjectSemanticResolver

from .go import GoSemanticResolver
from .go_environment import GoAnalysisConfig
from .typescript import TypeScriptSemanticResolver


def project_resolvers(file_languages: Mapping[str, str]) -> list[tuple[ProjectSemanticResolver, list[str]]]:
    """按已确定的语言范围选择后端；新增后端只改装配点，不改变依赖分析调用协议。"""
    return [
        (resolver, paths)
        for languages, resolver in (
            ({"javascript", "typescript"}, TypeScriptSemanticResolver()),
            ({"go"}, GoSemanticResolver()),
        )
        if (paths := sorted(path for path, language in file_languages.items() if language in languages))
    ]


__all__ = ["GoAnalysisConfig", "GoSemanticResolver", "TypeScriptSemanticResolver", "project_resolvers"]
