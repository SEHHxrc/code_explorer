"""最小跨语言程序图的应用服务。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import IGNORED_SOURCE_DIRECTORIES
from backend.app.services.syntax_analysis.session import reuse_syntax

from .contracts import ProgramGraphArtifact, ProgramGraphCoverage
from .frontends import (
    CppProgramGraphFrontend,
    CProgramGraphFrontend,
    GoProgramGraphFrontend,
    JavaProgramGraphFrontend,
    JavaScriptProgramGraphFrontend,
    PythonProgramGraphFrontend,
    RustProgramGraphFrontend,
    TypeScriptProgramGraphFrontend,
)
from .passes import ControlFlowGraphBuilder, ReachingDefinitionsPass, ValueFlowPass
from .registry import ProgramGraphFrontendRegistry
from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact
from backend.app.services.semantic_index.projection import ProgramFacts
from .passes.declaration_bindings import DeclarationBindingPass
from .passes.selectors import SelectorResolutionPass
from .passes.control_dependence import ControlDependencePass

DEFAULT_LIMITATIONS = [
    "当前为最小 CPG 公共层，持久化函数分区、CFG、词法变量级到达定义和值流变量映射。",
    "支配、后支配与控制依赖相对于现有 CFG；路径条件仅支持有界标量模型，不证明任意程序或部署安全。",
    "调用目标继续由 dependency_analyzer 负责；本模块不重复解析 CALLS 关系。",
]


class ProgramGraphService:
    """编排语言前端、通用 CFG 和到达定义 Overlay。"""

    def __init__(
        self,
        *,
        frontends: ProgramGraphFrontendRegistry | None = None,
        cfg_builder_factory: Callable[[], ControlFlowGraphBuilder] | None = None,
        reaching_definitions: ReachingDefinitionsPass | None = None,
        value_flow: ValueFlowPass | None = None,
    ) -> None:
        """注入可扩展前端注册表和公共 Pass。"""
        self.frontends = frontends or ProgramGraphFrontendRegistry([
            PythonProgramGraphFrontend(),
            JavaProgramGraphFrontend(),
            JavaScriptProgramGraphFrontend(),
            TypeScriptProgramGraphFrontend(),
            GoProgramGraphFrontend(),
            CProgramGraphFrontend(),
            CppProgramGraphFrontend(),
            RustProgramGraphFrontend(),
        ])
        self.cfg_builder_factory = cfg_builder_factory or ControlFlowGraphBuilder
        self.reaching_definitions = reaching_definitions or ReachingDefinitionsPass()
        self.value_flow = value_flow or ValueFlowPass()

    @reuse_syntax
    def analyze(
        self,
        project_root: str | Path,
        *,
        dependency_graph: dict[str, Any] | None = None,
        languages: set[str] | None = None,
        semantic_index: SemanticIndex | SemanticIndexArtifact | None = None,
    ) -> ProgramGraphArtifact:
        """为已支持且被请求的语言构建按函数分区的最小程序图。"""
        root = Path(project_root)
        bindings = DeclarationBindingPass(semantic_index) if semantic_index is not None else None
        files_by_language = self._files_by_language(
            root,
            dependency_graph,
            languages=languages,
            semantic_index=semantic_index,
        )
        functions = {}
        failures: list[dict[str, object]] = []
        files_considered = 0
        files_parsed = 0
        for language in sorted(files_by_language):
            frontend = self.frontends.get(language)
            if frontend is None:
                continue
            paths = files_by_language[language]
            if not paths:
                continue
            frontend_result = frontend.build(root, file_paths=paths)
            files_considered += frontend_result.files_considered
            files_parsed += frontend_result.files_parsed
            failures.extend(frontend_result.failures)
            for function in frontend_result.functions:
                if function.method_id in functions:
                    failures.append({
                        "path": function.location.path,
                        "line": function.location.line,
                        "language": function.language,
                        "reason": "duplicate_method_identity",
                        "method_id": function.method_id,
                    })
                    continue
                try:
                    cfg = self.cfg_builder_factory().build(function)
                    if bindings is not None:
                        cfg = bindings.apply(cfg)
                    cfg = SelectorResolutionPass().apply(cfg)
                    definitions = self.reaching_definitions.apply(cfg)
                    functions[function.method_id] = ControlDependencePass().apply(self.value_flow.apply(definitions))
                except (TypeError, ValueError, RuntimeError) as exc:
                    failures.append({
                        "path": function.location.path,
                        "line": function.location.line,
                        "language": function.language,
                        "reason": "function_graph_error",
                        "method_id": function.method_id,
                        "detail": type(exc).__name__,
                    })
        cfg_edges = sum(
            edge.kind == "cfg"
            for function in functions.values()
            for edge in function.edges
        )
        reaching_edges = sum(
            edge.kind == "reaching_def"
            for function in functions.values()
            for edge in function.edges
        )
        value_flow_edges = sum(
            edge.kind == "value_flow"
            for function in functions.values()
            for edge in function.edges
        )
        return ProgramGraphArtifact(
            **({"overlays": ["base", "cfg", "declaration_bindings", "reaching_definitions", "value_flow", "control_dependence"]}
               if bindings is not None else {}),
            languages=sorted(files_by_language),
            functions=functions,
            coverage=ProgramGraphCoverage(
                files_considered=files_considered,
                files_parsed=files_parsed,
                parse_failures=sum(
                    item.get("reason") in {"parse_error", "partial_parse_error"}
                    for item in failures
                ),
                function_count=len(functions),
                cfg_node_count=sum(len(item.nodes) for item in functions.values()),
                cfg_edge_count=cfg_edges,
                reaching_def_edge_count=reaching_edges,
                value_flow_edge_count=value_flow_edges,
                control_dependence_edge_count=sum(edge.kind == "control_dependence" for function in functions.values() for edge in function.edges),
            ),
            failures=failures,
            limitations=list(DEFAULT_LIMITATIONS),
        )

    def _files_by_language(
        self,
        root: Path,
        dependency_graph: dict[str, Any] | None,
        *,
        languages: set[str] | None = None,
        semantic_index: SemanticIndex | SemanticIndexArtifact | None = None,
    ) -> dict[str, list[str]]:
        """优先复用依赖图文件范围，否则按已注册扩展名发现文件。"""
        grouped: dict[str, set[str]] = defaultdict(set)
        registered = set(self.frontends.languages())
        allowed = registered if languages is None else registered.intersection(languages)
        files = ProgramFacts(semantic_index, dependency_graph).files_by_language()
        if files is not None:
            return {language: [ProgramIdentity.file_id(path) for path in paths]
                    for language, paths in files.items() if language in allowed}

        extensions = {
            extension: language
            for language in allowed
            for extension in self.frontends.get(language).extensions  # type: ignore[union-attr]
        }
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            relative = path.relative_to(root)
            if any(part in IGNORED_SOURCE_DIRECTORIES for part in relative.parts):
                continue
            language = extensions[path.suffix.lower()]
            grouped[language].add(relative.as_posix())
        return {language: sorted(paths) for language, paths in grouped.items()}
