"""依赖分析阶段 mixin 共享的宿主状态与跨阶段能力契约。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import networkx as nx

from backend.app.services.syntax_analysis import TreeSitterParserPool

from .models import Definition, ImportRec, Reference


class DependencyAnalyzerState:
    """声明阶段实现所依赖的共享状态，不持有独立运行逻辑。

    ``UnifiedCodeAnalyzer`` 负责初始化字段；各阶段继承本类只为形成可检查的
    mixin 契约。跨阶段方法在实际 MRO 中由相应阶段实现覆盖。
    """

    BUILTIN_PREFIX: str
    STDLIB_PREFIX: str
    EXTERNAL_PREFIX: str
    MAX_VIRTUAL_TARGETS: int

    project_root: str
    max_workers: int
    max_file_bytes: int
    include_builtins: bool
    include_externals: bool
    include_fields: bool
    include_type_refs: bool
    include_virtual_dispatch: bool

    global_graph: nx.MultiDiGraph
    file_symbols_map: dict[str, list[dict[str, Any]]]
    definitions: dict[str, Definition]
    references: list[Reference]
    file_lang: dict[str, str]
    file_package: dict[str, str]
    module_scope: dict[str, dict[str, str]]
    class_members: dict[str, dict[str, str]]
    classes_by_file: dict[str, dict[str, str]]
    simple_index: dict[str, list[str]]
    class_simple_index: dict[str, list[str]]
    py_modules: dict[str, list[str]]
    rs_modules: dict[str, list[str]]
    js_modules: dict[str, str]
    go_dir_files: dict[str, list[str]]
    rs_dir_files: dict[str, list[str]]
    go_pkg_dirs: dict[str, list[str]]
    java_fqcn: dict[str, str]
    java_pkg_classes: dict[str, dict[str, str]]
    c_files_by_name: dict[str, list[str]]
    attr_types: dict[str, dict[str, str]]
    alias_map: dict[str, str]
    class_alias: dict[str, str]
    class_bases: dict[str, list[str]]
    subclasses: dict[str, set[str]]
    _mro_cache: dict[str, list[str]]
    _ref_var_types: dict[str, dict[str, str]]
    _go_dir_cache: dict[tuple[str, str], str]
    _include_cache: dict[tuple[str, str], str]
    _type_cache: dict[tuple[str, str], str]
    bindings: dict[str, dict[str, tuple[Any, ...]]]
    file_imports: dict[str, list[ImportRec]]
    parsed_files_count: int
    total_files_count: int
    _progress_lock: Any
    _diagnostics_lock: Any
    _parser_pool: TreeSitterParserPool
    stats: defaultdict[str, int]
    global_index: dict[str, Any]
    diagnostics: dict[str, list[Any]]

    def _canonical(self, fqn: str) -> str:
        """返回经过别名归一化的符号标识。"""
        raise NotImplementedError

    def _real_class(self, fqn: str) -> str:
        """返回占位类对应的真实类型标识。"""
        raise NotImplementedError

    def _mro(self, class_fqn: str) -> list[str]:
        """返回类型及其祖先的保守解析顺序。"""
        raise NotImplementedError

    def _lookup_member(self, class_fqn: str, name: str) -> str:
        """在类型及其祖先中查找成员。"""
        raise NotImplementedError

    def _lookup_attr_type(self, class_fqn: str, attr: str) -> str:
        """查找成员字段的已知类型。"""
        raise NotImplementedError

    def _resolve_type(self, from_file: str, literal: str, lang: str, _depth: int = 0) -> str:
        """把语言类型字面量解析为项目内类型标识。"""
        raise NotImplementedError

    def _lookup_package_scope(
        self,
        from_file: str,
        name: str,
        lang: str,
        classes_only: bool = False,
    ) -> str:
        """在文件所属包或模块作用域查找名称。"""
        raise NotImplementedError

    def _go_package_dir(self, from_file: str, alias: str) -> str:
        """解析 Go 导入别名对应的包目录。"""
        raise NotImplementedError

    def _go_package_symbol(self, directory: str, name: str) -> str:
        """在 Go 包目录中查找导出符号。"""
        raise NotImplementedError

    def _resolve_include(self, from_file: str, include_path: str) -> str:
        """解析 C/C++ include 指向的项目文件。"""
        raise NotImplementedError

    def _lookup_in_module(self, target_file: str, symbol: str, lang: str) -> str:
        """在已解析模块中查找符号。"""
        raise NotImplementedError

    def _member_in_module(self, module_file: str, name: str) -> str:
        """在模块节点下查找成员定义。"""
        raise NotImplementedError

    @staticmethod
    def _best_module_match(candidates: list[str], from_file: str) -> str:
        """从候选模块中选择与来源文件最接近的项目模块。"""
        raise NotImplementedError
