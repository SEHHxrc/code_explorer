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

class GraphResolutionPhase:
    """图阶段：生成节点并解析定义、重写、调用和外部依赖边。"""

    def _build_graph_nodes(self):
        """将全部确定性定义转换为 NetworkX 图节点。"""
        graph = self.global_graph
        for path, lang in self.file_lang.items():
            graph.add_node(path, type="module", name=os.path.basename(path), level="module",
                           lang=lang, file=path, kind="module")
        for fqn, definition in self.definitions.items():
            if self._canonical(fqn) != fqn:
                continue
            if definition.kind in ("field", "variable", "constant", "macro") and not self.include_fields:
                continue
            level = GRAPH_LEVEL.get(definition.kind, "variable")
            graph.add_node(fqn, type=definition.kind, name=definition.name, level=level,
                           kind=definition.kind, lang=definition.lang, file=definition.file,
                           line=definition.line, declaration=definition.is_declaration)

    def _link_definitions(self):
        graph = self.global_graph
        for fqn, definition in self.definitions.items():
            canonical = self._canonical(fqn)
            if canonical != fqn or not graph.has_node(canonical):
                continue
            parent = self._canonical(definition.parent_fqn)
            parent_def = self.definitions.get(parent)
            if not graph.has_node(parent):
                parent, parent_def = definition.file, None
            if graph.has_node(parent) and parent != canonical:
                graph.add_edge(parent, canonical, relation="contains")
                self.stats["edges_contains"] += 1
            # 成员定义在别的文件里（Go 方法 / C++ 类外定义）：补一条物理归属边
            if (parent_def is not None and parent_def.file != definition.file
                    and graph.has_node(definition.file)):
                graph.add_edge(definition.file, canonical, relation="declares")
                self.stats["edges_declares"] += 1

        for class_fqn, bases in self.class_bases.items():
            source = self._canonical(class_fqn)
            if not graph.has_node(source):
                continue
            for base in bases:
                target = self._canonical(base)
                if not graph.has_node(target) or target == source:
                    continue
                base_kind = self.definitions.get(base).kind if base in self.definitions else "class"
                relation = "implements" if base_kind in ("interface", "trait") else "inherits"
                if self.definitions.get(source) is not None and self.definitions[source].lang == "go" \
                        and base_kind in ("struct", "type"):
                    relation = "embeds"
                graph.add_edge(source, target, relation=relation)
                self.stats[f"edges_{relation}"] += 1

    def _resolve_overrides(self):
        """比较类层级成员并向图中追加方法重写关系。"""
        graph = self.global_graph
        for class_fqn in list(self.class_bases):
            for name, member_fqn in self.class_members.get(class_fqn, {}).items():
                definition = self.definitions.get(member_fqn)
                if definition is None or definition.kind not in ("method", "constructor"):
                    continue
                for base in self._mro(class_fqn)[1:]:
                    base_member = self.class_members.get(base, {}).get(name)
                    if not base_member:
                        continue
                    source = self._canonical(member_fqn)
                    target = self._canonical(base_member)
                    if source != target and graph.has_node(source) and graph.has_node(target):
                        graph.add_edge(source, target, relation="overrides")
                        self.stats["edges_overrides"] += 1
                    break

    def _resolve_references(self):
        """结合导入、作用域和类型索引解析引用并追加调用、实例化和类型关系。"""
        graph = self.global_graph
        for ref in self.references:
            if ref.kind == "implements":
                continue
            source = self._canonical(ref.from_fqn)
            if not graph.has_node(source):
                source = ref.file
                if not graph.has_node(source):
                    continue
            if ref.kind == "typeref":
                if not self.include_type_refs:
                    continue
                target = self._resolve_type(ref.file, ref.name, ref.lang)
                if target and graph.has_node(target) and target != source:
                    graph.add_edge(source, target, relation="uses")
                    self.stats["edges_uses"] += 1
                continue

            target, dispatch = self._resolve_reference(ref)
            if target is None:
                self.stats["unresolved"] += 1
                if len(self.global_index["unresolved"]) < 500:
                    self.global_index["unresolved"].append(
                        {"file": ref.file, "line": ref.line, "name": ref.name, "receiver": ref.receiver})
                continue
            if not graph.has_node(target) or target == source:
                continue
            target_def = self.definitions.get(target)
            if ref.kind == "new" or (target_def is not None and target_def.kind in CLASS_LIKE):
                relation = "instantiates"          # Python/TS 里 ``Foo()`` 就是实例化
            else:
                relation = "calls"
            graph.add_edge(source, target, relation=relation, dispatch=dispatch)
            self.stats[f"edges_{relation}"] += 1
            self.stats[f"resolved_{dispatch}"] += 1

            if self.include_virtual_dispatch and relation == "calls":
                self._expand_virtual(graph, source, target)

    def _expand_virtual(self, graph, source: str, target: str):
        """多态：调用点连向基类方法时，同时连向各子类的覆写实现。"""
        definition = self.definitions.get(target)
        if definition is None or definition.kind not in ("method", "constructor"):
            return
        owner = definition.parent_fqn
        if owner not in self.subclasses:
            return
        expanded = 0
        for subclass in self.subclasses.get(owner, ()):
            override = self.class_members.get(subclass, {}).get(definition.name)
            if not override:
                continue
            override = self._canonical(override)
            if override in (target, source) or not graph.has_node(override):
                continue
            if graph.has_edge(source, override):
                continue
            graph.add_edge(source, override, relation="calls", dispatch="dynamic")
            self.stats["edges_calls_dynamic"] += 1
            expanded += 1
            if expanded >= self.MAX_VIRTUAL_TARGETS:
                self.stats["virtual_truncated"] += 1
                break

    def _resolve_reference(self, ref: Reference):
        """返回 (目标节点 id 或 None, 派发方式)。目标可能是内置/外部虚拟节点。"""
        lang = ref.lang
        handler = get_handler(lang)
        name, receiver = ref.name, ref.receiver
        file = ref.file
        class_fqn = self._real_class(ref.class_fqn) if ref.class_fqn else ""

        if ref.kind == "new":
            literal = f"{receiver}.{name}" if receiver else name
            target = self._resolve_type(file, literal, lang)
            if target:
                return target, "static"
            return self._fallback_symbol(ref, handler, prefer_type=True)

        # ---- 有接收者 ----
        if receiver:
            parts = _split_qualified(receiver)
            head = parts[0]

            if handler and head in handler.self_names and class_fqn:
                if len(parts) > 1:                       # self.field.method()
                    attr_type = self._lookup_attr_type(class_fqn, parts[1])
                    attr_class = self._resolve_type(file, attr_type, lang) if attr_type else ""
                    if attr_class:
                        hit = self._lookup_member(attr_class, name)
                        if hit:
                            return hit, "static"
                hit = self._lookup_member(class_fqn, name)
                if hit:
                    return hit, "static"

            if handler and head in handler.super_names and class_fqn:
                for base in self._mro(class_fqn)[1:]:
                    hit = self.class_members.get(base, {}).get(name)
                    if hit:
                        return self._canonical(hit), "static"

            var_type = self._var_type_of(ref, head)
            if var_type:
                type_fqn = self._resolve_type(file, var_type, lang)
                if type_fqn:
                    if len(parts) > 1:
                        attr_type = self._lookup_attr_type(type_fqn, parts[1])
                        nested = self._resolve_type(file, attr_type, lang) if attr_type else ""
                        if nested:
                            hit = self._lookup_member(nested, name)
                            if hit:
                                return hit, "static"
                    hit = self._lookup_member(type_fqn, name)
                    if hit:
                        return hit, "static"

            binding = self.bindings.get(file, {}).get(head)
            if binding:
                btype, payload, extra = binding
                if btype == "module" and payload:
                    hit = self._lookup_in_module(payload, name, lang)
                    if hit:
                        return self._canonical(hit), "static"
                    hit = self._member_in_module(payload, name)
                    if hit:
                        return hit, "static"
                elif btype == "symbol" and payload:
                    hit = self._lookup_member(payload, name)
                    if hit:
                        return hit, "static"
                    return self._canonical(payload), "static"

            # Go 的包名前缀：包 = 目录，需要在退回“外部依赖”之前先查本项目的包
            if lang == "go":
                pkg_dir = self._go_package_dir(file, head)
                if pkg_dir:
                    hit = self._go_package_symbol(pkg_dir, name)
                    if hit:
                        return self._canonical(hit), "static"

            if binding and binding[0] in ("external_module", "external_symbol", "stdlib_module", "stdlib_symbol"):
                # 保留完整前缀：os + ".path" -> os.path
                module = (binding[1] or binding[2] or head) + receiver[len(head):]
                is_stdlib = binding[0].startswith("stdlib")
                return self._external_node(module, name, is_stdlib=is_stdlib), "stdlib" if is_stdlib else "external"

            # 接收者是本项目里的类型名（静态方法 / 关联函数）
            type_fqn = self._resolve_type(file, receiver, lang)
            if type_fqn:
                hit = self._lookup_member(type_fqn, name)
                if hit:
                    return hit, "static"
                # 命名空间只是前缀，连到它没有意义；类则退化为“用到了这个类”
                if self.definitions[type_fqn].kind != "namespace":
                    return type_fqn, "static"

            if handler and handler.is_builtin_call(name, receiver):
                return self._builtin_node(lang, f"{receiver}.{name}"), "builtin"

            return self._fallback_member(ref, handler)

        # ---- 无接收者 ----
        if handler and handler.bare_call_hits_class and class_fqn:
            hit = self._lookup_member(class_fqn, name)
            if hit:
                return hit, "static"

        hit = self.module_scope.get(file, {}).get(name)
        if hit:
            return self._canonical(hit), "static"

        binding = self.bindings.get(file, {}).get(name)
        if binding:
            btype, payload, extra = binding
            if btype == "symbol" and payload:
                return self._canonical(payload), "static"
            if btype in ("external_symbol", "stdlib_symbol"):
                is_stdlib = btype == "stdlib_symbol"
                return self._external_node(payload, extra or name, is_stdlib=is_stdlib), "stdlib" if is_stdlib else "external"
            if btype == "module" and payload:
                return payload, "static"
            if btype in ("external_module", "stdlib_module"):
                is_stdlib = btype == "stdlib_module"
                return self._external_node(payload, name, is_stdlib=is_stdlib), "stdlib" if is_stdlib else "external"

        hit = self._lookup_package_scope(file, name, lang)
        if hit:
            return self._canonical(hit), "static"

        if handler and handler.is_builtin_call(name, ""):
            return self._builtin_node(lang, name), "builtin"

        wildcard = self.bindings.get(file, {}).get("*")
        if wildcard:
            if wildcard[1]:
                hit = self._lookup_in_module(wildcard[1], name, lang)
                if hit:
                    return self._canonical(hit), "static"
            elif self.include_externals:
                return self._external_node(wildcard[2], name), "external"

        return self._fallback_symbol(ref, handler)

    def _var_type_of(self, ref: Reference, name: str) -> str:
        """查阶段一记录的局部变量 / 参数 / 接收者类型表。"""
        hit = self._ref_var_types.get(ref.from_fqn, {}).get(name)
        if hit:
            return hit
        return self._ref_var_types.get(ref.file, {}).get(name, "")

    def _member_in_module(self, module_file: str, name: str) -> str:
        """模块级找不到时，看是不是模块里某个类的静态成员（Java 的 Helper.log 等）。"""
        for class_fqn in self.classes_by_file.get(module_file, {}).values():
            hit = self.class_members.get(class_fqn, {}).get(name)
            if hit:
                return self._canonical(hit)
        return ""

    def _fallback_symbol(self, ref: Reference, handler, prefer_type: bool = False):
        """全项目简名唯一匹配（弱推断），否则归入内置/外部/未解析。"""
        pool = self.class_simple_index if prefer_type else self.simple_index
        candidates = [fqn for fqn in pool.get(ref.name, [])
                      if self.definitions[fqn].kind in (CLASS_LIKE if prefer_type
                                                        else {"function", "method", "constructor"})]
        canonical = {self._canonical(fqn) for fqn in candidates}
        if len(canonical) == 1:
            return next(iter(canonical)), "heuristic"
        if handler and handler.is_builtin_call(ref.name, ref.receiver):
            return self._builtin_node(ref.lang, ref.name), "builtin"
        if canonical:
            best_file = self._best_module_match([self.definitions[f].file for f in candidates], ref.file)
            for fqn in candidates:
                if self.definitions[fqn].file == best_file:
                    return self._canonical(fqn), "heuristic"
        return None, "unresolved"

    def _fallback_member(self, ref: Reference, handler):
        """带接收者但接收者类型未知：先判内置类型方法，再退化为“项目里唯一同名方法”。"""
        if handler and ref.name in handler.type_methods:
            return self._builtin_node(ref.lang, ref.name), "builtin"
        candidates = {self._canonical(fqn) for fqn in self.simple_index.get(ref.name, [])
                      if self.definitions[fqn].kind in ("method", "constructor")}
        if len(candidates) == 1:
            return next(iter(candidates)), "heuristic"
        if handler and handler.is_builtin_call(ref.name, ref.receiver):
            return self._builtin_node(ref.lang, ref.name), "builtin"
        return None, "unresolved"

    def _builtin_node(self, lang: str, name: str) -> str | None:
        if not self.include_builtins:
            return None
        node_id = f"{self.BUILTIN_PREFIX}::{lang}::{name}"
        if not self.global_graph.has_node(node_id):
            self.global_graph.add_node(node_id, type="builtin", name=name, level="builtin",
                                       kind="builtin", lang=lang, file="")
            self.stats["builtin_nodes"] += 1
        return node_id

    def _external_node(self, module: str, name: str, *, is_stdlib: bool = False) -> str | None:
        if not self.include_externals:
            return None
        module = module or "unknown"
        prefix = self.STDLIB_PREFIX if is_stdlib else self.EXTERNAL_PREFIX
        module_id = f"{prefix}::{module}"
        module_type = "stdlib_module" if is_stdlib else "external_module"
        module_level = "stdlib_module" if is_stdlib else "external_module"
        if not self.global_graph.has_node(module_id):
            self.global_graph.add_node(module_id, type=module_type, name=module,
                                       level=module_level, kind=module_type, file="",
                                       dependency_scope="stdlib" if is_stdlib else "third_party")
            self.stats["stdlib_modules" if is_stdlib else "external_modules"] += 1
        if not name:
            return module_id
        node_id = f"{module_id}::{name}"
        node_type = "stdlib" if is_stdlib else "external"
        if not self.global_graph.has_node(node_id):
            self.global_graph.add_node(node_id, type=node_type, name=name, level=node_type,
                                       kind=node_type, module=module, file="",
                                       dependency_scope="stdlib" if is_stdlib else "third_party")
            self.global_graph.add_edge(module_id, node_id, relation="contains")
            self.stats["stdlib_nodes" if is_stdlib else "external_nodes"] += 1
        return node_id
