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

class ImportResolutionPhase:
    """导入阶段：解析各语言模块、包和头文件绑定。"""

    def _resolve_imports(self):
        """把每个文件的导入语句解析成本地名 -> 目标（模块文件 / 具体符号 / 外部包）的绑定。"""
        for path, records in self.file_imports.items():
            lang = self.file_lang.get(path, "")
            handler = get_handler(lang)
            for rec in records:
                target_file = self._resolve_module(path, lang, rec)
                if target_file:
                    self.global_graph.add_edge(path, target_file, relation="imports")
                    self.stats["edges_imports"] += 1
                if rec.kind == "wildcard":
                    self.bindings[path]["*"] = ("wildcard", target_file, rec.module)
                    continue
                if rec.symbol and rec.kind == "symbol":
                    fqn = self._lookup_in_module(target_file, rec.symbol, lang) if target_file else ""
                    if fqn:
                        self.bindings[path][rec.alias] = ("symbol", fqn, rec.module)
                        self.stats["imports_resolved"] += 1
                    else:
                        is_std = bool(handler and handler.is_stdlib_module(rec.module)) or rec.kind == "system"
                        binding_type = "stdlib_symbol" if is_std else "external_symbol"
                        self.bindings[path][rec.alias] = (binding_type, rec.module, rec.symbol)
                        self.stats["imports_external"] += 1
                else:
                    if target_file:
                        self.bindings[path][rec.alias] = ("module", target_file, rec.module)
                        self.stats["imports_resolved"] += 1
                    else:
                        is_std = bool(handler and handler.is_stdlib_module(rec.module)) or rec.kind == "system"
                        binding_type = "stdlib_module" if is_std else "external_module"
                        self.bindings[path][rec.alias] = (binding_type, rec.module, "std" if is_std else "")
                        self.stats["imports_external"] += 1

    def _resolve_module(self, from_file: str, lang: str, rec: ImportRec) -> str:
        module = rec.module
        if not module:
            return ""
        if lang == "python":
            return self._resolve_python_module(from_file, module, rec)
        if lang in ("javascript", "typescript"):
            return self._resolve_js_module(from_file, module)
        if lang == "go":
            return ""      # Go 以目录为单位，符号查找时再定位（见 _go_package_dir）
        if lang == "java":
            return self._resolve_java_module(module, rec)
        if lang in ("c", "cpp"):
            if rec.kind == "system":
                return ""
            return self._resolve_include(from_file, module)
        if lang == "rust":
            return self._resolve_rust_module(from_file, module, rec)
        return ""

    def _resolve_python_module(self, from_file: str, module: str, rec: ImportRec) -> str:
        if module.startswith("."):
            level = len(module) - len(module.lstrip("."))
            remainder = module[level:]
            base = os.path.dirname(from_file)
            for _ in range(level - 1):
                base = os.path.dirname(base)
            parts = [p for p in base.split("/") if p] + [p for p in remainder.split(".") if p]
            candidate = "/".join(parts)
            for suffix in (".py", "/__init__.py"):
                if candidate + suffix in self.file_lang:
                    return candidate + suffix
            # from . import x —— 目标是包目录里的模块
            if rec.symbol:
                for suffix in (".py", "/__init__.py"):
                    guess = "/".join(parts + [rec.symbol]) + suffix
                    if guess in self.file_lang:
                        return guess
            return ""
        candidates = self.py_modules.get(module, [])
        if not candidates and "." in module:
            # 分析根目录可能位于包根之下（如只上传了 backend/），逐级去掉前缀再试
            parts = module.split(".")
            for start in range(1, len(parts)):
                candidates = self.py_modules.get(".".join(parts[start:]), [])
                if candidates:
                    break
        return self._best_module_match(candidates, from_file)

    def _resolve_rust_module(self, from_file: str, module: str, rec: ImportRec) -> str:
        cleaned = module.replace("crate::", "").replace("self::", "").replace("super::", "")
        cleaned = cleaned.strip(": ")
        if not cleaned:
            cleaned = rec.symbol
        candidates = self.rs_modules.get(cleaned, [])
        if not candidates and "::" in cleaned:
            candidates = self.rs_modules.get(cleaned.split("::")[-1], [])
        return self._best_module_match(candidates, from_file)

    def _resolve_js_module(self, from_file: str, module: str) -> str:
        if not module.startswith("."):
            return ""
        base = os.path.dirname(from_file)
        joined = os.path.normpath(os.path.join(base, module)).replace("\\", "/")
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue"):
            if joined + suffix in self.file_lang:
                return joined + suffix
        hit = self.js_modules.get(joined)
        if hit:
            return hit
        for suffix in ("/index.ts", "/index.js", "/index.tsx", "/index.jsx"):
            if joined + suffix in self.file_lang:
                return joined + suffix
        return ""

    def _resolve_java_module(self, module: str, rec: ImportRec) -> str:
        fqcn = f"{module}.{rec.symbol}" if rec.symbol and rec.kind == "symbol" else module
        target = self.java_fqcn.get(fqcn)
        if target:
            return self.definitions[target].file
        classes = self.java_pkg_classes.get(module)
        if classes and rec.symbol in classes:
            return self.definitions[classes[rec.symbol]].file
        return ""

    def _resolve_include(self, from_file: str, include_path: str) -> str:
        """``#include`` 定位。会被 include 链反复调用，故按 (来源文件, 头文件) 记忆化。"""
        cache_key = (from_file, include_path)
        cached = self._include_cache.get(cache_key)
        if cached is not None:
            return cached
        self._include_cache[cache_key] = result = self._compute_include(from_file, include_path)
        return result

    def _compute_include(self, from_file: str, include_path: str) -> str:
        include_path = include_path.replace("\\", "/")
        base = os.path.dirname(from_file)
        candidate = os.path.normpath(os.path.join(base, include_path)).replace("\\", "/")
        if candidate in self.file_lang:
            return candidate
        if include_path in self.file_lang:
            return include_path
        for path in self.file_lang:
            if path.endswith("/" + include_path):
                return path
        hits = self.c_files_by_name.get(os.path.basename(include_path), [])
        return hits[0] if hits else ""

    @staticmethod
    def _best_module_match(candidates: list[str], from_file: str) -> str:
        if not candidates:
            return ""
        if len(candidates) == 1:
            return candidates[0]
        from_parts = from_file.split("/")

        def score(path: str) -> tuple:
            """输入候选路径，输出与引用文件目录接近程度的排序元组。"""
            parts = path.split("/")
            common = 0
            for a, b in zip(parts, from_parts):
                if a != b:
                    break
                common += 1
            return common, -len(parts)

        return max(candidates, key=score)

    def _lookup_in_module(self, target_file: str, symbol: str, lang: str) -> str:
        if not target_file:
            return ""
        hit = self.module_scope.get(target_file, {}).get(symbol)
        if hit:
            return hit
        # Java：一个文件可能有多个顶层类型
        hit = self.classes_by_file.get(target_file, {}).get(symbol)
        if hit:
            return hit
        if lang in ("javascript", "typescript") and symbol == "default":
            module_defs = self.module_scope.get(target_file, {})
            if len(module_defs) == 1:
                return next(iter(module_defs.values()))
        return self._member_in_module(target_file, symbol)
