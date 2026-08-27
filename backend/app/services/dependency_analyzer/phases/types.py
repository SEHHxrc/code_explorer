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

class TypeResolutionPhase:
    """类型阶段：解析继承、成员、返回类型、包作用域和方法解析顺序。"""

    def _resolve_inheritance(self):
        """解析类的基类和接口字面量，输出继承映射与子类反向索引。"""
        for fqn, definition in self.definitions.items():
            if definition.kind not in CLASS_LIKE:
                continue
            for base_literal in definition.bases:
                base_fqn = self._resolve_type(definition.file, base_literal, definition.lang)
                if base_fqn and base_fqn != fqn:
                    self.class_bases[fqn].append(base_fqn)
                    self.subclasses[base_fqn].add(fqn)
            for itf_literal in definition.implements:
                itf_fqn = self._resolve_type(definition.file, itf_literal, definition.lang)
                if itf_fqn and itf_fqn != fqn:
                    self.class_bases[fqn].append(itf_fqn)
                    self.subclasses[itf_fqn].add(fqn)

        # Rust: impl Trait for Type
        for ref in self.references:
            if ref.kind != "implements":
                continue
            type_fqn = self._resolve_type(ref.file, ref.class_fqn.split("::")[-1], ref.lang)
            trait_fqn = self._resolve_type(ref.file, ref.name, ref.lang)
            if type_fqn and trait_fqn and type_fqn != trait_fqn:
                self.class_bases[type_fqn].append(trait_fqn)
                self.subclasses[trait_fqn].add(type_fqn)

        # 把跨文件定义的成员（Go 方法 / Rust impl / C++ 类外定义）挂接到真正的类型上
        for fqn, definition in self.definitions.items():
            if not definition.owner_literal or definition.kind not in ("method", "constructor", "function"):
                continue
            owner_fqn = self._resolve_type(definition.file, definition.owner_literal, definition.lang)
            if not owner_fqn or owner_fqn == fqn:
                continue
            canonical = self.class_members.get(owner_fqn, {}).get(definition.name)
            if canonical and canonical != fqn:
                # 同名成员已存在（例如 C++ 类内声明 + 类外定义）—— 合并到同一节点
                if self.definitions[canonical].is_declaration and not definition.is_declaration:
                    self.definitions[canonical].line = definition.line
                    self.definitions[canonical].end_line = definition.end_line
                    self.definitions[canonical].file = definition.file
                    self.definitions[canonical].is_declaration = False
                self.alias_map[fqn] = canonical
            else:
                self.class_members[owner_fqn][definition.name] = fqn
            if definition.parent_fqn != owner_fqn:
                # 阶段一用的是 "<文件>::<类型名>" 占位前缀，这里登记成真实类型
                self.class_alias[definition.parent_fqn] = owner_fqn
            definition.parent_fqn = owner_fqn

        # C/C++：头文件里的原型 -> 源文件里的实现
        for fqn, definition in self.definitions.items():
            if definition.lang not in ("c", "cpp") or not definition.is_declaration:
                continue
            if definition.kind not in ("function", "method"):
                continue
            impl = self._find_c_implementation(definition)
            if impl and impl != fqn:
                self.alias_map[fqn] = impl

    def _find_c_implementation(self, declaration: Definition) -> str:
        candidates = [fqn for fqn in self.simple_index.get(declaration.name, [])
                      if fqn != declaration.fqn
                      and self.definitions[fqn].lang in ("c", "cpp")
                      and not self.definitions[fqn].is_declaration
                      and self.definitions[fqn].kind in ("function", "method")]
        if not candidates:
            return ""
        if len(candidates) == 1:
            return candidates[0]
        stem = os.path.splitext(declaration.file)[0]
        for fqn in candidates:
            if os.path.splitext(self.definitions[fqn].file)[0] == stem:
                return fqn
        return candidates[0]

    def _canonical(self, fqn: str) -> str:
        seen = 0
        while fqn in self.alias_map and seen < 8:
            fqn = self.alias_map[fqn]
            seen += 1
        return fqn

    def _real_class(self, fqn: str) -> str:
        """把阶段一的占位类作用域（Go 方法 / Rust impl / C++ 类外定义）换成真实类型定义。"""
        seen = 0
        while fqn in self.class_alias and seen < 8:
            fqn = self.class_alias[fqn]
            seen += 1
        return self._canonical(fqn)

    def _mro(self, class_fqn: str) -> list[str]:
        cached = self._mro_cache.get(class_fqn)
        if cached is not None:
            return cached
        order, seen = [], {class_fqn}
        queue = deque([class_fqn])
        while queue:
            current = queue.popleft()
            order.append(current)
            for base in self.class_bases.get(current, ()):
                if base not in seen:
                    seen.add(base)
                    queue.append(base)
        self._mro_cache[class_fqn] = order
        return order

    def _lookup_member(self, class_fqn: str, name: str) -> str:
        for candidate in self._mro(class_fqn):
            hit = self.class_members.get(candidate, {}).get(name)
            if hit:
                return self._canonical(hit)
        return ""

    def _lookup_attr_type(self, class_fqn: str, attr: str) -> str:
        for candidate in self._mro(class_fqn):
            hit = self.attr_types.get(candidate, {}).get(attr)
            if hit:
                return hit
        return ""

    def _resolve_type(self, from_file: str, literal: str, lang: str, _depth: int = 0) -> str:
        """把类型字面量解析成本项目内的 class-like 定义。同一文件里同名类型反复出现，故记忆化。"""
        if literal.startswith(CALL_TYPE_PREFIX):
            if _depth > 2:
                return ""
            return self._type_from_call(from_file, literal[len(CALL_TYPE_PREFIX):], lang, _depth + 1)
        cache_key = (from_file, literal)
        cached = self._type_cache.get(cache_key)
        if cached is not None:
            return cached
        self._type_cache[cache_key] = result = self._compute_type(from_file, literal, lang)
        return result

    def _compute_type(self, from_file: str, literal: str, lang: str) -> str:
        literal = _norm_type(literal)
        if not literal:
            return ""
        parts = _split_qualified(literal)
        simple = parts[-1]
        qualifier = ".".join(parts[:-1])

        if qualifier:
            binding = self.bindings.get(from_file, {}).get(parts[0])
            if binding and binding[0] == "module" and binding[1]:
                hit = self._as_class(self._lookup_in_module(binding[1], simple, lang))
                if hit:
                    return hit
            if lang == "java":
                hit = self._as_class(self.java_fqcn.get(literal.replace("::", ".")))
                if hit:
                    return hit
            if lang == "go":
                pkg_dir = self._go_package_dir(from_file, parts[0])
                if pkg_dir:
                    hit = self._as_class(self._go_package_symbol(pkg_dir, simple))
                    if hit:
                        return hit

        binding = self.bindings.get(from_file, {}).get(simple)
        if binding and binding[0] == "symbol" and binding[1]:
            hit = self._as_class(binding[1])
            if hit:
                return hit

        hit = self._as_class(self.classes_by_file.get(from_file, {}).get(simple))
        if hit:
            return hit

        hit = self._as_class(self._lookup_package_scope(from_file, simple, lang, classes_only=True))
        if hit:
            return hit

        candidates = self.class_simple_index.get(simple, [])
        if len(candidates) == 1:
            return self._as_class(candidates[0])
        if candidates:
            best = self._best_module_match([self.definitions[c].file for c in candidates], from_file)
            for candidate in candidates:
                if self.definitions[candidate].file == best:
                    return self._as_class(candidate)
        return ""

    def _as_class(self, fqn: str) -> str:
        """类型解析只应返回 class-like 定义（结构体/类/接口/枚举/别名）。"""
        if not fqn:
            return ""
        fqn = self._canonical(fqn)
        definition = self.definitions.get(fqn)
        return fqn if definition is not None and definition.kind in CLASS_LIKE else ""

    def _type_from_call(self, from_file: str, callee: str, lang: str, depth: int) -> str:
        """``x = foo()`` —— 先看 foo 是不是构造器，再取 foo 的返回值类型。"""
        hit = self._resolve_type(from_file, callee, lang, depth)
        if hit:
            return hit
        callable_fqn = self._resolve_callable(from_file, callee, lang)
        if callable_fqn:
            return_type = self.definitions[callable_fqn].return_type
            if return_type:
                return self._resolve_type(from_file, return_type, lang, depth)
        return ""

    def _resolve_callable(self, from_file: str, literal: str, lang: str) -> str:
        """把一个调用字面量解析成项目内的函数/方法定义（用于返回值类型推断）。"""
        parts = _split_qualified(literal)
        if not parts:
            return ""
        simple, head = parts[-1], parts[0]
        if len(parts) > 1:
            binding = self.bindings.get(from_file, {}).get(head)
            if binding and binding[0] == "module" and binding[1]:
                hit = self._lookup_in_module(binding[1], simple, lang)
                if hit:
                    return self._canonical(hit)
            owner = self._resolve_type(from_file, ".".join(parts[:-1]), lang)
            if owner:
                hit = self._lookup_member(owner, simple)
                if hit:
                    return hit
            if lang == "go":
                pkg_dir = self._go_package_dir(from_file, head)
                if pkg_dir:
                    hit = self._go_package_symbol(pkg_dir, simple)
                    if hit:
                        return self._canonical(hit)
            return ""
        hit = self.module_scope.get(from_file, {}).get(simple)
        if hit:
            return self._canonical(hit)
        binding = self.bindings.get(from_file, {}).get(simple)
        if binding and binding[0] == "symbol" and binding[1]:
            return self._canonical(binding[1])
        hit = self._lookup_package_scope(from_file, simple, lang)
        if hit:
            return self._canonical(hit)
        candidates = {self._canonical(fqn) for fqn in self.simple_index.get(simple, [])
                      if self.definitions[fqn].kind in ("function", "method", "constructor")}
        return next(iter(candidates)) if len(candidates) == 1 else ""

    def _lookup_package_scope(self, from_file: str, name: str, lang: str, classes_only: bool = False) -> str:
        """同包/同目录/同头文件可见的符号（Go 同目录、Java 同包、C 的 include 链、Rust 同模块）。"""
        if lang == "go":
            directory = os.path.dirname(from_file)
            for path in self.go_dir_files.get(directory, ()):
                hit = self.module_scope.get(path, {}).get(name)
                if hit and (not classes_only or self.definitions[hit].kind in CLASS_LIKE):
                    return hit
            return ""
        if lang == "java":
            package = self.file_package.get(from_file, "")
            hit = self.java_pkg_classes.get(package, {}).get(name)
            if hit:
                return hit
            for rec in self.file_imports.get(from_file, ()):
                if rec.kind != "wildcard":
                    continue
                hit = self.java_pkg_classes.get(rec.module, {}).get(name)
                if hit:
                    return hit
            return ""
        if lang in ("c", "cpp"):
            for rec in self.file_imports.get(from_file, ()):
                if rec.kind == "system":
                    continue
                target = self._resolve_include(from_file, rec.module)
                if not target:
                    continue
                hit = (self.classes_by_file.get(target, {}).get(name) if classes_only
                       else self.module_scope.get(target, {}).get(name)
                       or self.classes_by_file.get(target, {}).get(name))
                if hit:
                    return hit
            # 同名的 .c/.h 配对
            stem = os.path.splitext(from_file)[0]
            for ext in (".c", ".cpp", ".cc", ".h", ".hpp"):
                sibling = stem + ext
                if sibling == from_file or sibling not in self.file_lang:
                    continue
                hit = (self.classes_by_file.get(sibling, {}).get(name) if classes_only
                       else self.module_scope.get(sibling, {}).get(name))
                if hit:
                    return hit
            return ""
        if lang == "rust":
            for path in self.rs_dir_files.get(os.path.dirname(from_file), ()):
                hit = self.module_scope.get(path, {}).get(name)
                if hit and (not classes_only or self.definitions[hit].kind in CLASS_LIKE):
                    return hit
            return ""
        if lang == "python":
            binding = self.bindings.get(from_file, {}).get("*")
            if binding and binding[1]:
                hit = self._lookup_in_module(binding[1], name, lang)
                if hit:
                    return hit
        return ""

    def _go_package_dir(self, from_file: str, alias: str) -> str:
        cache_key = (from_file, alias)
        if cache_key in self._go_dir_cache:
            return self._go_dir_cache[cache_key]
        self._go_dir_cache[cache_key] = result = self._compute_go_package_dir(from_file, alias)
        return result

    def _compute_go_package_dir(self, from_file: str, alias: str) -> str:
        binding = self.bindings.get(from_file, {}).get(alias)
        module = binding[1] if binding and binding[0] in ("external_module", "stdlib_module", "module") else alias
        if binding and binding[0] == "module" and module in self.file_lang:
            return os.path.dirname(module)
        module_path = (module or "").replace("\\", "/")
        best, best_len = "", -1
        segments = [s for s in module_path.split("/") if s]
        for directory in self.go_dir_files:
            dir_segments = [s for s in directory.split("/") if s]
            match = 0
            while (match < len(segments) and match < len(dir_segments)
                   and segments[-1 - match] == dir_segments[-1 - match]):
                match += 1
            if match > best_len and match > 0:
                best, best_len = directory, match
        if best:
            return best
        for directory in self.go_pkg_dirs.get(alias, ()):
            return directory
        return ""

    def _go_package_symbol(self, directory: str, name: str) -> str:
        for path in self.go_dir_files.get(directory, ()):
            hit = self.module_scope.get(path, {}).get(name)
            if hit:
                return hit
            hit = self.classes_by_file.get(path, {}).get(name)
            if hit:
                return hit
        return ""
