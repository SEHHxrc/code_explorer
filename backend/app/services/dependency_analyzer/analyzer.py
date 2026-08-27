# -*- coding: utf-8 -*-
from __future__ import annotations

"""多语言依赖分析器的稳定门面和流水线编排。"""
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import networkx as nx

from .constants import EXT_MAP
from .models import Definition, ImportRec, Reference
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
    MAX_VIRTUAL_TARGETS = 8

    def __init__(self, project_root: str, max_workers: int = 4, *,
                 max_file_bytes: int = 2 * 1024 * 1024,
                 include_builtins: bool = True,
                 include_externals: bool = True,
                 include_fields: bool = True,
                 include_type_refs: bool = True,
                 include_virtual_dispatch: bool = True):
        self.project_root = os.path.abspath(project_root)
        self.max_workers = max(1, int(max_workers or 1))
        self.max_file_bytes = max_file_bytes
        self.include_builtins = include_builtins
        self.include_externals = include_externals
        self.include_fields = include_fields
        self.include_type_refs = include_type_refs
        self.include_virtual_dispatch = include_virtual_dispatch

        self.global_graph = nx.DiGraph()
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
        self._tls = threading.local()

        self.stats = defaultdict(int)
        self.global_index = {"imports": {}, "unresolved": []}

    def run_full_analysis(self) -> dict:
        """扫描项目并执行收集、索引和关系解析，输出符号表、依赖图与统计。"""
        target_files = self._collect_files()
        self.total_files_count = len(target_files)
        if not target_files:
            return {"file_symbols": {}, "dependency_graph": nx.node_link_data(self.global_graph),
                    "stats": dict(self.stats)}

        contexts = []
        if self.max_workers > 1 and len(target_files) > 1:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self._analyze_file, path): path for path in target_files}
                for future in as_completed(futures):
                    try:
                        ctx = future.result()
                        if ctx is not None:
                            contexts.append(ctx)
                    except Exception as exc:            # 单文件失败不影响整体
                        print(f"[Error] analysis task failed for {futures[future]}: {exc}")
        else:
            for path in target_files:
                try:
                    ctx = self._analyze_file(path)
                    if ctx is not None:
                        contexts.append(ctx)
                except Exception as exc:
                    print(f"[Error] analysis task failed for {path}: {exc}")

        self._merge_contexts(contexts)
        self._build_indexes()
        self._resolve_imports()
        self._resolve_inheritance()
        self._build_graph_nodes()
        self._link_definitions()
        self._resolve_overrides()
        self._resolve_references()

        return {
            "file_symbols": self.file_symbols_map,
            "dependency_graph": nx.node_link_data(self.global_graph),
            "stats": dict(self.stats),
        }

    def get_progress(self) -> dict:
        """输出总文件数和已解析文件数的线程安全进度快照。"""
        return {"total_files": self.total_files_count, "parsed_files": self.parsed_files_count}
