# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
import traceback
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import networkx as nx
from tree_sitter_language_pack import get_parser

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..handlers import get_handler
from ..models import Definition, ImportRec, Reference

class IndexingPhase:
    """索引阶段：建立语言无关和语言特定的全局符号索引。"""

    def _build_indexes(self):
        """输入阶段一合并结果，构建模块、类、简单名和类型等跨文件解析索引。"""
        for path, lang in self.file_lang.items():
            self._index_file_module(path, lang)

        for fqn, definition in self.definitions.items():
            parent = definition.parent_fqn
            kind = definition.kind
            if kind in CLASS_LIKE:
                self.classes_by_file[definition.file][definition.name] = fqn
                self.class_simple_index[definition.name].append(fqn)
            if parent == definition.file:
                self.module_scope[definition.file][definition.name] = fqn
            self.simple_index[definition.name].append(fqn)

        # 类成员表（词法归属）
        for fqn, definition in self.definitions.items():
            parent = definition.parent_fqn
            if parent in self.definitions and self.definitions[parent].kind in CLASS_LIKE:
                self.class_members[parent][definition.name] = fqn
                if definition.kind == "field" and definition.type_literal:
                    self.attr_types[parent][definition.name] = definition.type_literal

        # Java FQCN
        for fqn, definition in self.definitions.items():
            if definition.lang != "java" or definition.kind not in CLASS_LIKE:
                continue
            package = self.file_package.get(definition.file, "")
            fqcn = f"{package}.{definition.name}" if package else definition.name
            self.java_fqcn[fqcn] = fqn
            self.java_pkg_classes[package][definition.name] = fqn

        # Go：目录即包
        for path, lang in self.file_lang.items():
            if lang != "go":
                continue
            directory = os.path.dirname(path)
            self.go_dir_files[directory].append(path)
            package = self.file_package.get(path, "")
            if package:
                self.go_pkg_dirs[package].append(directory)

        for path, lang in self.file_lang.items():
            if lang == "rust":
                self.rs_dir_files[os.path.dirname(path)].append(path)

        # C/C++：按文件名索引，便于 #include 与头/源配对
        for path, lang in self.file_lang.items():
            if lang not in ("c", "cpp"):
                continue
            self.c_files_by_name[os.path.basename(path)].append(path)

    def _index_file_module(self, path: str, lang: str):
        if lang == "python":
            stem = path[:-3] if path.endswith(".py") else path
            if stem.endswith("/__init__"):
                stem = stem[: -len("/__init__")]
            parts = [p for p in stem.split("/") if p]
            for start in range(len(parts)):
                self.py_modules[".".join(parts[start:])].append(path)
        elif lang == "rust":
            stem = path[:-3] if path.endswith(".rs") else path
            parts = [p for p in stem.split("/") if p]
            if parts and parts[-1] in ("mod", "lib", "main"):
                parts = parts[:-1]
            for start in range(len(parts)):
                self.rs_modules["::".join(parts[start:])].append(path)
        elif lang in ("javascript", "typescript"):
            stem = os.path.splitext(path)[0]
            self.js_modules.setdefault(stem, path)
            if stem.endswith("/index"):
                self.js_modules.setdefault(stem[: -len("/index")], path)
