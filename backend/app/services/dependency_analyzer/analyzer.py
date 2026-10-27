"""多语言依赖分析器的稳定门面和流水线编排。"""

from __future__ import annotations

import os
import logging
import threading
from contextvars import copy_context
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import networkx as nx

from backend.app.services.semantic_index import (
    DependencyAnalysisSemanticProvider,
    SemanticIndexProvider,
)
from backend.app.services.semantic_index.resolution import ProjectSemanticResolver
from backend.app.services.language_backends import project_resolvers
from backend.app.services.syntax_analysis import DEFAULT_TREE_SITTER_PARSER_POOL
from backend.app.services.syntax_analysis.session import reuse_syntax
from backend.app.services.syntax_analysis.javascript import parser_for_file

from .constants import EXT_MAP
from .models import Definition, ImportRec, Reference
from .compiler import CompilerBindingIndex

logger = logging.getLogger(__name__)
from .phases import (
    CollectionPhase,
    GraphResolutionPhase,
    ImportResolutionPhase,
    IndexingPhase,
    TypeResolutionPhase,
)


class UnifiedCodeAnalyzer(
    CollectionPhase,
    IndexingPhase,
    ImportResolutionPhase,
    TypeResolutionPhase,
    GraphResolutionPhase,
):
    """统一静态代码分析门面；按固定阶段输出符号、依赖图与统计。"""

    EXT_MAP = EXT_MAP
    BUILTIN_PREFIX = "<builtin>"
    STDLIB_PREFIX = "<stdlib>"
    EXTERNAL_PREFIX = "<external>"

    def __init__(self, project_root: str, max_workers: int = 4, *,
                 max_file_bytes: int = 2 * 1024 * 1024,
                 include_builtins: bool = True,
                 include_externals: bool = True,
                 include_fields: bool = True,
                 include_type_refs: bool = True,
                 include_virtual_dispatch: bool = True,
                 max_virtual_targets: int = 128,
                 semantic_index_provider: SemanticIndexProvider | None = None,
                 semantic_resolver: ProjectSemanticResolver | None = None) -> None:
        """输入项目根目录、并发上限和图节点选项，初始化分析流水线。"""
        self.project_root = os.path.abspath(project_root)
        self.max_workers = max(1, int(max_workers or 1))
        self.max_file_bytes = max_file_bytes
        self.include_builtins = include_builtins
        self.include_externals = include_externals
        self.include_fields = include_fields
        self.include_type_refs = include_type_refs
        self.include_virtual_dispatch = include_virtual_dispatch
        if type(max_virtual_targets) is not int or not 1 <= max_virtual_targets <= 512:
            raise ValueError("虚调用目标预算必须为 1～512 的整数")
        self.max_virtual_targets = max_virtual_targets
        self.semantic_index_provider = (
            semantic_index_provider or DependencyAnalysisSemanticProvider()
        )
        self.semantic_resolver = semantic_resolver
        self._compiler_bindings: CompilerBindingIndex | None = None

        # 同一节点对可能在不同调用点、以不同关系相连；内部分析图必须无损保存。
        # 前端展示所需的聚合由 GraphExchangeNormalizer 单独负责。
        self.global_graph = nx.MultiDiGraph()
        self.file_symbols_map: dict[str, list] = {}

        # 阶段一产出
        self.definitions: dict[str, Definition] = {}
        self.references: list[Reference] = []
        self.file_lang: dict[str, str] = {}
        self.file_package: dict[str, str] = {}

        # 阶段二索引
        self.module_scope: dict[str, dict[str, str]] = defaultdict(dict)
        self.class_members: dict[str, dict[str, str]] = defaultdict(dict)
        self.classes_by_file: dict[str, dict[str, str]] = defaultdict(dict)
        self.simple_index: dict[str, list[str]] = defaultdict(list)
        self.class_simple_index: dict[str, list[str]] = defaultdict(list)
        self.py_modules: dict[str, list[str]] = defaultdict(list)
        self.rs_modules: dict[str, list[str]] = defaultdict(list)
        self.js_modules: dict[str, str] = {}
        self.go_dir_files: dict[str, list[str]] = defaultdict(list)
        self.rs_dir_files: dict[str, list[str]] = defaultdict(list)
        self.go_pkg_dirs: dict[str, list[str]] = defaultdict(list)
        self.java_fqcn: dict[str, str] = {}
        self.java_pkg_classes: dict[str, dict[str, str]] = defaultdict(dict)
        self.c_files_by_name: dict[str, list[str]] = defaultdict(list)
        self.attr_types: dict[str, dict[str, str]] = defaultdict(dict)
        self.alias_map: dict[str, str] = {}
        self.class_alias: dict[str, str] = {}      # 占位类作用域 -> 真实类型定义
        self.class_bases: dict[str, list[str]] = defaultdict(list)
        self.subclasses: dict[str, set] = defaultdict(set)
        self._mro_cache: dict[str, list[str]] = {}
        self._ref_var_types: dict = defaultdict(dict)
        self._go_dir_cache: dict = {}
        self._include_cache: dict = {}
        self._type_cache: dict = {}

        # 阶段三绑定
        self.bindings: dict[str, dict[str, tuple]] = defaultdict(dict)
        self.file_imports: dict[str, list[ImportRec]] = defaultdict(list)

        self.parsed_files_count = 0
        self.total_files_count = 0
        self._progress_lock = threading.Lock()
        self._progress_stage = "扫描源码文件"
        self._diagnostics_lock = threading.Lock()
        self._parser_pool = DEFAULT_TREE_SITTER_PARSER_POOL

        self.stats = defaultdict(int)
        self.global_index = {"imports": {}, "unresolved": []}
        self.diagnostics = {
            "unresolved_references": self.global_index["unresolved"],
            "parse_failures": [],
            "skipped_files": [],
            "truncations": [],
        }

    @reuse_syntax
    def run_full_analysis(self) -> dict:
        """扫描项目并执行收集、索引和关系解析，输出符号表、依赖图与统计。"""
        target_files = self._collect_files()
        with self._progress_lock:
            self.total_files_count = len(target_files)
            self._progress_stage = "解析源码文件（包含失败或跳过的尝试）"
        if not target_files:
            return {
                "file_symbols": {},
                "dependency_graph": self._graph_payload(),
                "semantic_index": self._build_semantic_index(),
                "stats": dict(self.stats),
                "diagnostics": self._diagnostics_snapshot(),
            }

        self._set_progress_stage("检查本地语法解析器（不联网）")
        self._parser_pool.ensure_available({
            parser_for_file(language, path)
            for path in target_files
            if (language := self._detect_lang(path)) is not None
        })
        self._set_progress_stage("解析源码文件（包含失败或跳过的尝试）")

        contexts = []
        if self.max_workers > 1 and len(target_files) > 1:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(copy_context().run, self._analyze_file, path): path for path in target_files}
                for future in as_completed(futures):
                    try:
                        ctx = future.result()
                        if ctx is not None:
                            contexts.append(ctx)
                    except Exception:            # 单文件失败不影响整体
                        logger.warning("analysis.file_task_failed", extra={"source_file": os.path.relpath(futures[future], self.project_root)}, exc_info=True)
        else:
            for path in target_files:
                try:
                    ctx = self._analyze_file(path)
                    if ctx is not None:
                        contexts.append(ctx)
                except Exception:
                    logger.warning("analysis.file_task_failed", extra={"source_file": os.path.relpath(path, self.project_root)}, exc_info=True)

        self._set_progress_stage("合并符号和建立索引")
        self._merge_contexts(contexts)
        self._build_indexes()
        self._set_progress_stage("解析导入和继承关系")
        self._resolve_imports()
        self._resolve_inheritance()
        self._set_progress_stage("构建节点和定义关系")
        self._build_graph_nodes()
        self._link_definitions()
        self._resolve_overrides()
        semantic_files = sorted(path for path, language in self.file_lang.items()
                                if language in {"javascript", "typescript", "go"})
        if semantic_files:
            resolvers = ([(self.semantic_resolver, semantic_files)] if self.semantic_resolver is not None
                         else project_resolvers(self.file_lang))
            compiler_results = []
            for resolver, paths in resolvers:
                self._set_progress_stage("解析语言编译器语义与 Go SSA（只读）")
                compiler_result = resolver.resolve(
                    self.project_root, file_paths=paths, max_file_bytes=self.max_file_bytes)
                compiler_results.append(compiler_result)
                if compiler_result.status in {"failed", "unavailable"}:
                    logger.warning("analysis.semantic_backend_fallback", extra={
                        "semantic_backend": compiler_result.backend,
                        "semantic_status": compiler_result.status,
                        "reason": compiler_result.diagnostics[0].get("reason") if compiler_result.diagnostics else "unknown"})
            self._compiler_bindings = CompilerBindingIndex(compiler_results, self.definitions)
            self.diagnostics["semantic_backends"] = [result.summary() for result in compiler_results]
        self._set_progress_stage("解析调用、引用和派发关系")
        self._resolve_references()

        self._set_progress_stage("生成依赖图和语义索引产物")
        return {
            "file_symbols": self.file_symbols_map,
            "dependency_graph": self._graph_payload(),
            "semantic_index": self._build_semantic_index(),
            "stats": dict(self.stats),
            "diagnostics": self._diagnostics_snapshot(),
        }

    def _graph_payload(self) -> dict:
        """固定内部产物的 links 契约，隔离 NetworkX 新版默认改名为 edges 的变化。"""
        payload = nx.node_link_data(self.global_graph)
        if "edges" in payload:
            payload["links"] = payload.pop("edges")
        return payload

    def _build_semantic_index(self) -> dict:
        """把已有解析状态投影为公共语义事实，不再次读取或解析源码。"""
        artifact = self.semantic_index_provider.build(
            definitions=self.definitions,
            references=self.references,
            variable_types=self._ref_var_types,
            graph=self.global_graph,
            unresolved=self.global_index["unresolved"],
        )
        if self._compiler_bindings is not None:
            artifact = self._compiler_bindings.enrich(artifact)
            self.diagnostics["semantic_backends"] = artifact.backend_runs
        return artifact.model_dump()

    def _diagnostics_snapshot(self) -> dict:
        """返回可持久化的诊断快照，并补充完整计数与截断状态。"""
        unresolved = list(self.diagnostics["unresolved_references"])
        return {
            "unresolved_references": unresolved,
            "parse_failures": list(self.diagnostics["parse_failures"]),
            "skipped_files": list(self.diagnostics["skipped_files"]),
            "truncations": list(self.diagnostics["truncations"]),
            "semantic_backends": list(self.diagnostics.get("semantic_backends", [])),
            "coverage": {
                "total_files": self.total_files_count,
                "attempted_files": self.parsed_files_count,
                "parsed_files": int(self.stats.get("files_parsed", 0)),
                "failed_files": int(self.stats.get("parse_failures", 0)),
                "partial_failure_count": int(self.stats.get("partial_parse_failures", 0)),
                "unresolved_total": int(self.stats.get("unresolved", 0)),
                "unresolved_stored": len(unresolved),
                "unresolved_truncated": False,
            },
        }

    def get_progress(self) -> dict:
        """输出线程安全的文件尝试计数及阶段；文件 100% 不表示关系解析完成。"""
        with self._progress_lock:
            return {
                "total_files": self.total_files_count,
                "parsed_files": self.parsed_files_count,
                "stage_label": self._progress_stage,
            }

    def _set_progress_stage(self, label: str) -> None:
        """输入不含源码路径的阶段标签，供 get_progress 查询。"""
        with self._progress_lock:
            self._progress_stage = label
