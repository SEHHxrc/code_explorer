from __future__ import annotations

import hashlib
import os

import networkx as nx

from backend.app.services.program_index import ProgramIdentity

from ..ast_utils import _split_qualified
from ..constants import CLASS_LIKE, GRAPH_LEVEL
from ..handlers import BaseHandler, get_handler
from ..models import Reference, ReferenceResolution
from ..state import DependencyAnalyzerState


class GraphResolutionPhase(DependencyAnalyzerState):
    """图阶段：生成节点并解析定义、重写、调用和外部依赖边。"""

    def _build_graph_nodes(self) -> None:
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

    def _link_definitions(self) -> None:
        """连接模块、类型和成员之间的结构关系。"""
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
                base_definition = self.definitions.get(base)
                base_kind = base_definition.kind if base_definition is not None else "class"
                relation = "implements" if base_kind in ("interface", "trait") else "inherits"
                if self.definitions.get(source) is not None and self.definitions[source].lang == "go" \
                        and base_kind in ("struct", "type"):
                    relation = "embeds"
                graph.add_edge(source, target, relation=relation)
                self.stats[f"edges_{relation}"] += 1

    def _resolve_overrides(self) -> None:
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

    def _resolve_references(self) -> None:
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

            resolution = self._resolve_reference(ref)
            target = resolution.target
            if target is None:
                self.stats["unresolved"] += 1
                self.global_index["unresolved"].append({
                    "callsite_id": ProgramIdentity.callsite_id(
                        ref.file, ref.line, ref.column, ref.end_line, ref.end_column
                    ),
                    "file": ref.file,
                    "line": ref.line,
                    "column": ref.column,
                    "end_line": ref.end_line,
                    "end_column": ref.end_column,
                    "from_fqn": ref.from_fqn,
                    "kind": ref.kind,
                    "name": ref.name,
                    "receiver": ref.receiver,
                    "resolution_method": resolution.resolution_method,
                    "unresolved_reason": resolution.unresolved_reason or "symbol_not_found",
                    "target_certainty": "unresolved",
                    "confidence": resolution.confidence,
                })
                continue
            if not graph.has_node(target) or target == source:
                continue
            target_def = self.definitions.get(target)
            if ref.kind == "new" or (target_def is not None and target_def.kind in CLASS_LIKE):
                relation = "instantiates"  # Python/TS 里 ``Foo()`` 就是实例化
            else:
                relation = "calls"
            graph.add_edge(
                source,
                target,
                id=self._reference_edge_id(ref, source, target, relation, resolution),
                relation=relation,
                **self._reference_edge_attributes(ref, resolution),
            )
            self.stats[f"edges_{relation}"] += 1
            self.stats[f"resolved_{resolution.resolution_method}"] += 1

            if self.include_virtual_dispatch and relation == "calls":
                self._expand_virtual(graph, source, target, ref)

    @staticmethod
    def _reference_edge_attributes(ref: Reference, resolution: ReferenceResolution) -> dict:
        """把引用位置和解析不确定性转换为可持久化边属性。"""
        callsite_id = ProgramIdentity.callsite_id(
            ref.file, ref.line, ref.column, ref.end_line, ref.end_column
        )
        location_id = ProgramIdentity.location_id(
            ref.file, ref.line, ref.column, ref.end_line, ref.end_column
        )
        return {
            "callsite_id": callsite_id,
            "dispatch": resolution.dispatch,
            "target_scope": resolution.target_scope,
            "resolution_method": resolution.resolution_method,
            "target_certainty": resolution.target_certainty,
            "confidence": resolution.confidence,
            "truncated": resolution.truncated,
            "unresolved_reason": resolution.unresolved_reason,
            "origin": "inferred",
            "callsite": {
                "id": callsite_id,
                "location_id": location_id,
                "path": ref.file,
                "line": ref.line,
                "column": ref.column,
                "end_line": ref.end_line,
                "end_column": ref.end_column,
            },
            "reference": {
                "kind": ref.kind,
                "name": ref.name,
                "receiver": ref.receiver,
            },
        }

    @staticmethod
    def _reference_edge_id(ref: Reference, source: str, target: str, relation: str, resolution: ReferenceResolution,) -> str:
        """根据源码位置和解析目标生成跨运行稳定的引用边标识。"""
        material = "\x1f".join((
            ProgramIdentity.callsite_id(ref.file, ref.line, ref.column, ref.end_line, ref.end_column),
            source,
            target,
            relation,
            resolution.resolution_method,
        ))
        return "ref:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def _expand_virtual(self, graph: nx.MultiDiGraph, source: str, target: str, ref: Reference) -> None:
        """多态：调用点连向基类方法时，同时连向各子类的覆写实现。"""
        definition = self.definitions.get(target)
        if definition is None or definition.kind not in ("method", "constructor"):
            return
        owner = definition.parent_fqn
        if owner not in self.subclasses:
            return
        overrides: list[str] = []
        seen: set[str] = set()
        for subclass in self.subclasses.get(owner, ()):
            override = self.class_members.get(subclass, {}).get(definition.name)
            if not override:
                continue
            override = self._canonical(override)
            if override in (target, source) or override in seen or not graph.has_node(override):
                continue
            seen.add(override)
            overrides.append(override)
        truncated = len(overrides) > self.MAX_VIRTUAL_TARGETS
        for override in overrides[:self.MAX_VIRTUAL_TARGETS]:
            resolution = ReferenceResolution(
                target=override,
                dispatch="virtual",
                target_scope="project",
                resolution_method="virtual_expansion",
                target_certainty="may",
                confidence="medium",
                truncated=truncated,
            )
            graph.add_edge(
                source,
                override,
                id=self._reference_edge_id(ref, source, override, "calls", resolution),
                relation="calls",
                **self._reference_edge_attributes(ref, resolution),
            )
            self.stats["edges_calls_dynamic"] += 1
        if truncated:
            self.stats["virtual_truncated"] += 1
            self.diagnostics["truncations"].append({
                "kind": "virtual_targets",
                "file": ref.file,
                "line": ref.line,
                "available": len(overrides),
                "retained": self.MAX_VIRTUAL_TARGETS,
            })

    @staticmethod
    def _resolution(
            target: str | None,
            method: str,
            *,
            dispatch: str = "direct",
            target_scope: str = "project",
            target_certainty: str = "must",
            confidence: str = "high",
            unresolved_reason: str | None = None,
    ) -> ReferenceResolution:
        """构造稳定的解析结果，避免把范围、派发与置信度混为一个字段。"""
        return ReferenceResolution(
            target=target,
            dispatch=dispatch,
            target_scope=target_scope,
            resolution_method=method,
            target_certainty=target_certainty if target is not None else "unresolved",
            confidence=confidence,
            unresolved_reason=unresolved_reason if target is None else None,
        )

    def _resolve_reference(self, ref: Reference) -> ReferenceResolution:
        """解析引用目标并返回派发、范围、方法及确定性元数据。"""
        lang = ref.lang
        handler = get_handler(lang)
        name, receiver = ref.name, ref.receiver
        file = ref.file
        class_fqn = self._real_class(ref.class_fqn) if ref.class_fqn else ""

        if ref.kind == "new":
            literal = f"{receiver}.{name}" if receiver else name
            target = self._resolve_type(file, literal, lang)
            if target:
                return self._resolution(target, "type_resolution")
            return self._fallback_symbol(ref, handler, prefer_type=True)

        # ---- 有接收者 ----
        if receiver:
            parts = _split_qualified(receiver)
            head = parts[0]

            if handler and head in handler.self_names and class_fqn:
                if len(parts) > 1:  # self.field.method()
                    attr_type = self._lookup_attr_type(class_fqn, parts[1])
                    attr_class = self._resolve_type(file, attr_type, lang) if attr_type else ""
                    if attr_class:
                        hit = self._lookup_member(attr_class, name)
                        if hit:
                            return self._resolution(hit, "receiver_attribute_type", confidence="medium")
                hit = self._lookup_member(class_fqn, name)
                if hit:
                    return self._resolution(hit, "class_scope")

            if handler and head in handler.super_names and class_fqn:
                for base in self._mro(class_fqn)[1:]:
                    hit = self.class_members.get(base, {}).get(name)
                    if hit:
                        return self._resolution(self._canonical(hit), "inheritance_lookup")

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
                                return self._resolution(hit, "receiver_attribute_type", confidence="medium")
                    hit = self._lookup_member(type_fqn, name)
                    if hit:
                        return self._resolution(hit, "receiver_type", confidence="medium")

            binding = self.bindings.get(file, {}).get(head)
            if binding:
                btype, payload, extra = binding
                if btype == "module" and payload:
                    hit = self._lookup_in_module(payload, name, lang)
                    if hit:
                        return self._resolution(self._canonical(hit), "module_binding")
                    hit = self._member_in_module(payload, name)
                    if hit:
                        return self._resolution(hit, "module_binding")
                elif btype == "symbol" and payload:
                    hit = self._lookup_member(payload, name)
                    if hit:
                        return self._resolution(hit, "imported_symbol")
                    return self._resolution(self._canonical(payload), "imported_symbol")

            # Go 的包名前缀：包 = 目录，需要在退回“外部依赖”之前先查本项目的包
            if lang == "go":
                pkg_dir = self._go_package_dir(file, head)
                if pkg_dir:
                    hit = self._go_package_symbol(pkg_dir, name)
                    if hit:
                        return self._resolution(self._canonical(hit), "package_scope")

            if binding and binding[0] in ("external_module", "external_symbol", "stdlib_module", "stdlib_symbol"):
                # 保留完整前缀：os + ".path" -> os.path
                module = (binding[1] or binding[2] or head) + receiver[len(head):]
                is_stdlib = binding[0].startswith("stdlib")
                target = self._external_node(module, name, is_stdlib=is_stdlib)
                return self._resolution(
                    target,
                    "external_binding",
                    target_scope="stdlib" if is_stdlib else "third_party",
                    unresolved_reason="excluded_scope",
                )

            # 接收者是本项目里的类型名（静态方法 / 关联函数）
            type_fqn = self._resolve_type(file, receiver, lang)
            if type_fqn:
                hit = self._lookup_member(type_fqn, name)
                if hit:
                    return self._resolution(hit, "receiver_type", confidence="medium")
                # 命名空间只是前缀，连到它没有意义；类则退化为“用到了这个类”
                if self.definitions[type_fqn].kind != "namespace":
                    return self._resolution(type_fqn, "receiver_type", confidence="medium")

            if handler and handler.is_builtin_call(name, receiver):
                return self._resolution(
                    self._builtin_node(lang, f"{receiver}.{name}"),
                    "builtin_catalog",
                    target_scope="builtin",
                    unresolved_reason="excluded_scope",
                )

            return self._fallback_member(ref, handler)

        # ---- 无接收者 ----
        if handler and handler.bare_call_hits_class and class_fqn:
            hit = self._lookup_member(class_fqn, name)
            if hit:
                return self._resolution(hit, "class_scope")

        hit = self.module_scope.get(file, {}).get(name)
        if hit:
            return self._resolution(self._canonical(hit), "local_scope")

        binding = self.bindings.get(file, {}).get(name)
        if binding:
            btype, payload, extra = binding
            if btype == "symbol" and payload:
                return self._resolution(self._canonical(payload), "imported_symbol")
            if btype in ("external_symbol", "stdlib_symbol"):
                is_stdlib = btype == "stdlib_symbol"
                target = self._external_node(payload, extra or name, is_stdlib=is_stdlib)
                return self._resolution(
                    target,
                    "external_binding",
                    target_scope="stdlib" if is_stdlib else "third_party",
                    unresolved_reason="excluded_scope",
                )
            if btype == "module" and payload:
                return self._resolution(payload, "module_binding")
            if btype in ("external_module", "stdlib_module"):
                is_stdlib = btype == "stdlib_module"
                target = self._external_node(payload, name, is_stdlib=is_stdlib)
                return self._resolution(
                    target,
                    "external_binding",
                    target_scope="stdlib" if is_stdlib else "third_party",
                    unresolved_reason="excluded_scope",
                )

        hit = self._lookup_package_scope(file, name, lang)
        if hit:
            return self._resolution(self._canonical(hit), "package_scope")

        if handler and handler.is_builtin_call(name, ""):
            return self._resolution(
                self._builtin_node(lang, name),
                "builtin_catalog",
                target_scope="builtin",
                unresolved_reason="excluded_scope",
            )

        wildcard = self.bindings.get(file, {}).get("*")
        if wildcard:
            if wildcard[1]:
                hit = self._lookup_in_module(wildcard[1], name, lang)
                if hit:
                    return self._resolution(
                        self._canonical(hit), "wildcard_import",
                        target_certainty="may", confidence="medium",
                    )
            elif self.include_externals:
                return self._resolution(
                    self._external_node(wildcard[2], name),
                    "wildcard_import",
                    target_scope="third_party",
                    target_certainty="may",
                    confidence="low",
                )

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

    def _fallback_symbol(self, ref: Reference, handler: BaseHandler | None, prefer_type: bool = False) -> ReferenceResolution:
        """全项目简名唯一匹配（弱推断），否则归入内置/外部/未解析。"""
        pool = self.class_simple_index if prefer_type else self.simple_index
        candidates = [fqn for fqn in pool.get(ref.name, [])
                      if self.definitions[fqn].kind in (CLASS_LIKE if prefer_type
                                                        else {"function", "method", "constructor"})]
        canonical = {self._canonical(fqn) for fqn in candidates}
        if len(canonical) == 1:
            return self._resolution(
                next(iter(canonical)), "unique_name_heuristic",
                target_certainty="may", confidence="low",
            )
        if handler and handler.is_builtin_call(ref.name, ref.receiver):
            return self._resolution(
                self._builtin_node(ref.lang, ref.name),
                "builtin_catalog",
                target_scope="builtin",
                unresolved_reason="excluded_scope",
            )
        if canonical:
            best_file = self._best_module_match([self.definitions[f].file for f in candidates], ref.file)
            for fqn in candidates:
                if self.definitions[fqn].file == best_file:
                    return self._resolution(
                        self._canonical(fqn), "nearest_module_heuristic",
                        target_certainty="may", confidence="low",
                    )
        return self._resolution(
            None,
            "unresolved",
            confidence="low",
            unresolved_reason="ambiguous_symbol" if canonical else "symbol_not_found",
        )

    def _fallback_member(self, ref: Reference, handler: BaseHandler | None) -> ReferenceResolution:
        """带接收者但接收者类型未知：先判内置类型方法，再退化为“项目里唯一同名方法”。"""
        if handler and ref.name in handler.type_methods:
            return self._resolution(
                self._builtin_node(ref.lang, ref.name),
                "builtin_catalog",
                target_scope="builtin",
                unresolved_reason="excluded_scope",
            )
        candidates = {self._canonical(fqn) for fqn in self.simple_index.get(ref.name, [])
                      if self.definitions[fqn].kind in ("method", "constructor")}
        if len(candidates) == 1:
            return self._resolution(
                next(iter(candidates)), "unique_member_heuristic",
                target_certainty="may", confidence="low",
            )
        if handler and handler.is_builtin_call(ref.name, ref.receiver):
            return self._resolution(
                self._builtin_node(ref.lang, ref.name),
                "builtin_catalog",
                target_scope="builtin",
                unresolved_reason="excluded_scope",
            )
        return self._resolution(
            None,
            "unresolved",
            confidence="low",
            unresolved_reason="ambiguous_member" if candidates else "unknown_receiver_type",
        )

    def _builtin_node(self, lang: str, name: str) -> str | None:
        """构造语言内置符号对应的图节点标识。"""
        if not self.include_builtins:
            return None
        node_id = f"{self.BUILTIN_PREFIX}::{lang}::{name}"
        if not self.global_graph.has_node(node_id):
            self.global_graph.add_node(node_id, type="builtin", name=name, level="builtin",
                                       kind="builtin", lang=lang, file="")
            self.stats["builtin_nodes"] += 1
        return node_id

    def _external_node(self, module: str, name: str, *, is_stdlib: bool = False) -> str | None:
        """构造第三方外部符号对应的图节点标识。"""
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
