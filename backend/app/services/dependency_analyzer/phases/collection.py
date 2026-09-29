from __future__ import annotations

import os
import traceback

import tree_sitter

from ..constants import CPP_MARKERS, EXT_MAP, IGNORED_DIRS, SKIP_CHILDREN
from ..context import FileContext
from ..handlers import get_handler
from ..state import DependencyAnalyzerState


class CollectionPhase(DependencyAnalyzerState):
    """收集阶段：扫描文件、并发解析语法树并合并单文件上下文。"""

    def _collect_files(self) -> list[str]:
        """收集符合大小、扩展名和忽略规则的源码文件。"""
        targets = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                lang = EXT_MAP.get(ext)
                if not lang:
                    continue
                if name.endswith((".min.js", ".min.ts", ".bundle.js", ".d.ts")):
                    continue
                full = os.path.join(root, name)
                try:
                    if os.path.getsize(full) > self.max_file_bytes:
                        self.stats["skipped_large_files"] += 1
                        self.diagnostics["skipped_files"].append({
                            "file": os.path.relpath(full, self.project_root).replace("\\", "/"),
                            "reason": "file_too_large",
                        })
                        continue
                except OSError as exc:
                    self.stats["skipped_unreadable_files"] += 1
                    self.diagnostics["skipped_files"].append({
                        "file": os.path.relpath(full, self.project_root).replace("\\", "/"),
                        "reason": "metadata_unavailable",
                        "detail": type(exc).__name__,
                    })
                    continue
                targets.append(full)
        return targets

    @staticmethod
    def _detect_lang(full_path: str) -> str | None:
        """``.h`` 在 C 与 C++ 之间靠同名源文件和内容特征判定，否则按扩展名。"""
        ext = os.path.splitext(full_path)[1].lower()
        lang = EXT_MAP.get(ext)
        if lang != "c" or ext != ".h":
            return lang
        stem = os.path.splitext(full_path)[0]
        for sibling_ext in (".cpp", ".cc", ".cxx", ".c++"):
            if os.path.exists(stem + sibling_ext):
                return "cpp"
        if os.path.exists(stem + ".c"):
            return "c"
        try:
            with open(full_path, "rb") as fh:
                head = fh.read(64 * 1024)
        except OSError:
            return "c"
        return "cpp" if any(marker in head for marker in CPP_MARKERS) else "c"

    def _get_parser(self, lang: str) -> tree_sitter.Parser:
        """获取并缓存当前语言的 Tree-sitter 解析器。"""
        return self._parser_pool.get(lang)

    def _analyze_file(self, full_path: str) -> FileContext | None:
        """解析单个源码文件并返回独立文件上下文。"""
        rel_path = os.path.relpath(full_path, self.project_root).replace("\\", "/")
        lang = self._detect_lang(full_path)
        if lang is None:
            return None
        ctx = None
        try:
            handler = get_handler(lang)
            if handler is None:
                return None
            with open(full_path, "rb") as fh:
                src = fh.read()
            tree = self._get_parser(lang).parse(src)
            ctx = FileContext(rel_path, full_path, lang, src, handler)
            if tree.root_node.has_error:
                ctx.diagnostics.append({
                    "file": rel_path,
                    "reason": "syntax_error",
                    "detail": "tree_sitter_recovery",
                })
            self._walk(ctx, tree.root_node)
        except Exception as exc:
            print(f"[Warning] parse error in {rel_path}: {exc}")
            with self._diagnostics_lock:
                self.diagnostics["parse_failures"].append({
                    "file": rel_path,
                    "reason": "parse_error",
                    "detail": type(exc).__name__,
                })
                self.stats["parse_failures"] += 1
            if os.environ.get("CODE_EXPLORER_DEBUG"):
                traceback.print_exc()
        finally:
            with self._progress_lock:
                self.parsed_files_count += 1
        return ctx

    @staticmethod
    def _walk(ctx: FileContext, root: tree_sitter.Node) -> None:
        """显式栈式单次遍历：每个节点只被访问一次，处理函数不重复递归子树。"""
        handlers = ctx.handler.handlers
        stack = [(root, False)]
        while stack:
            node, is_exit = stack.pop()
            if is_exit:
                ctx.pop()
                continue
            depth_before = len(ctx.frames)
            handler = handlers.get(node.type)
            flags = 0
            if handler is not None:
                try:
                    flags = handler(node, ctx) or 0
                except Exception as exc:
                    print(f"[Warning] handler {node.type} failed in {ctx.path}:{node.start_point[0] + 1}: {exc}")
                    ctx.diagnostics.append({
                        "file": ctx.path,
                        "line": node.start_point[0] + 1,
                        "reason": "handler_error",
                        "detail": node.type,
                    })
                    if os.environ.get("CODE_EXPLORER_DEBUG"):
                        traceback.print_exc()
            pushed = len(ctx.frames) - depth_before
            if flags & SKIP_CHILDREN:
                for _ in range(pushed):
                    ctx.pop()
                continue
            stack.extend((node, True) for _ in range(pushed))
            # 叶子节点占绝大多数，先看计数可以省掉一次 list 构造
            if node.named_child_count:
                children = node.named_children
                stack.extend((child, False) for child in reversed(children))

    def _merge_contexts(self, contexts: list[FileContext]) -> None:
        """合并各文件上下文并生成分析阶段统计。"""
        for ctx in contexts:
            if ctx.diagnostics:
                self.diagnostics["parse_failures"].extend(ctx.diagnostics)
                self.stats["partial_parse_failures"] += len(ctx.diagnostics)
            self.file_symbols_map[ctx.path] = ctx.symbols
            self.file_lang[ctx.path] = ctx.lang
            self.file_package[ctx.path] = ctx.package
            self.references.extend(ctx.refs)
            self.file_imports[ctx.path].extend(ctx.imports)
            for fqn, mapping in ctx.var_type_table.items():
                self._ref_var_types[fqn].update(mapping)
            for definition in ctx.defs:
                existing = self.definitions.get(definition.fqn)
                if existing is None:
                    self.definitions[definition.fqn] = definition
                else:
                    if existing.is_declaration and not definition.is_declaration:
                        self.definitions[definition.fqn] = definition
            self.stats["files_parsed"] += 1
            self.stats["definitions"] += len(ctx.defs)
            self.stats["references"] += len(ctx.refs)
        self.global_index["imports"] = {
            path: [{"module": rec.module, "alias": rec.alias, "symbol": rec.symbol, "kind": rec.kind}
                   for rec in recs]
            for path, recs in self.file_imports.items()
        }
