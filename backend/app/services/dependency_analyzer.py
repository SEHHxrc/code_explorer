import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import networkx as nx
import tree_sitter
from tree_sitter_language_pack import get_parser


class UnifiedCodeAnalyzer:
    """统一的静态代码分析引擎：

    单次解析文件，同时产出：
    1. 供前端展示的 GitHub 风格文件大纲符号 (Symbols)
    2. 供全局架构拓扑使用的 NetworkX 依赖图谱 (Nodes & Edges)
    """

    EXT_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
    }

    # 语法节点类型常量复用
    CLS_DEF = ["class_definition", "class_declaration", "struct_item", "type_specifier"]
    FUNC_DEF = [
        "function_definition",
        "method_definition",
        "method_declaration",
        "function_item",
        "function_declaration",
    ]
    GLOBAL_DEF = [
        "assignment",
        "lexical_declaration",
        "variable_declaration",
        "const_item",
    ]
    FUNC_CALL = ["call_expression", "call", "method_invocation"]

    # 名字与标识符常量复用
    NAME_DEC = ["identifier", "name", "type_identifier", "field_identifier"]

    # 降噪黑名单
    NOISE_VARIABLES = [
        "i", "j", "k", "x", "y", "z", "e", "ex", "f", "fp",
        "c", "n", "id", "res", "result", "val", "value", "key",
        "item", "items", "data", "tmp", "temp", "ret", "args",
        "kwargs", "self", "cls",
    ]

    def __init__(self, project_root: str, max_workers: int = 4):
        self.project_root = project_root
        self.max_workers = max_workers
        self.global_graph = nx.DiGraph()
        self.file_symbols_map = {}

        self.parsed_files_count = 0
        self.total_files_count = 0

    def run_full_analysis(self) -> dict:
        """执行全项目统一分析，返回 { "file_symbols": {...}, "dependency_graph": {...} }"""
        target_files = []
        for root, _, files in os.walk(self.project_root):
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in self.EXT_MAP:
                    target_files.append((root, file))

        self.total_files_count = len(target_files)
        if self.total_files_count == 0:
            return {
                "file_symbols": {},
                "dependency_graph": nx.node_link_data(self.global_graph),
            }

        # 线程池并发处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._process_single_file, root, file): file
                for root, file in target_files
            }

            for future in as_completed(futures):
                try:
                    rel_path, symbols, sub_nodes, sub_edges = future.result()
                    self.file_symbols_map[rel_path] = symbols
                    self.global_graph.add_nodes_from(sub_nodes)
                    self.global_graph.add_edges_from(sub_edges)
                except Exception as e:
                    print(f"[Error] File analysis task failed: {e}")
                finally:
                    self.parsed_files_count += 1

        return {
            "file_symbols": self.file_symbols_map,
            "dependency_graph": nx.node_link_data(self.global_graph),
        }

    def get_progress(self) -> dict:
        return {
            "total_files": self.total_files_count,
            "parsed_files": self.parsed_files_count,
        }

    def _process_single_file(self, root: str, file: str) -> tuple:
        """单个文件独立解析核心：同时产出 Symbols 和 SubGraph 边/节点"""
        full_path = os.path.join(root, file)
        rel_path = os.path.relpath(full_path, self.project_root).replace("\\", "/")
        _, ext = os.path.splitext(file)
        lang_name = self.EXT_MAP[ext.lower()]

        symbols = []
        sub_graph = nx.DiGraph()

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                code_bytes = f.read().encode("utf-8")

            parser = get_parser(lang_name)
            tree = parser.parse(code_bytes)

            # 注册模块节点
            sub_graph.add_node(rel_path, type="module", lang=lang_name, level="module")

            # 递归遍历 AST，复用同一套作用域逻辑
            scope_stack = [rel_path]
            self._walk_ast(tree.root_node, code_bytes, symbols, sub_graph, scope_stack)

        except Exception as e:
            print(f"[Warning] Parse error in {rel_path}: {e}")

        return (
            rel_path,
            symbols,
            list(sub_graph.nodes(data=True)),
            list(sub_graph.edges(data=True)),
        )

    def _walk_ast(self,node: tree_sitter.Node, code_bytes: bytes, symbols: list, sub_graph: nx.DiGraph, scope_stack: list,):
        if not node:
            return

        node_type = node.type
        scope_pushed = False

        # 1. 识别类或结构体 (Class & Struct)
        if node_type in self.CLS_DEF:
            name = self._extract_name(node, code_bytes)
            if name:
                scope_stack.append(name)
                scope_pushed = True

                # 图谱 FQN
                fqn = "::".join(scope_stack)
                sub_graph.add_node(fqn, type="class", name=name, level="class")
                parent_module = scope_stack[0]
                sub_graph.add_edge(parent_module, fqn, relation="contains")

                # 符号大纲
                sym_fqn = f"{'.'.join(scope_stack[1:-1])}.{name}" if len(scope_stack) > 2 else name
                symbols.append(self._build_symbol(node, name, "class", sym_fqn))

                # 提取类内部成员
                self._extract_class_members(node, code_bytes, symbols, sub_graph, scope_stack)

        # 2. 识别函数或方法 (Function & Method)
        elif node_type in self.FUNC_DEF:
            name = self._extract_name(node, code_bytes)
            if name:
                # 判断全局还是类方法
                parent_is_class = False
                if len(scope_stack) > 1:
                    parent_id = "::".join(scope_stack)
                    if sub_graph.nodes.get(parent_id, {}).get("type") == "class":
                        parent_is_class = True

                is_global = len(scope_stack) == 1 or not parent_is_class

                if is_global:
                    fqn = f"{scope_stack[0]}::{name}"
                    sub_graph.add_node(fqn, type="function", name=name, level="function")
                    sub_graph.add_edge(scope_stack[0], fqn, relation="contains")
                else:
                    fqn = "::".join(scope_stack + [name])
                    sub_graph.add_node(fqn, type="method", name=name, level="method")
                    parent_class = "::".join(scope_stack)
                    sub_graph.add_edge(parent_class, fqn, relation="contains")

                # 符号大纲全限定名
                sym_fqn = f"{'.'.join(scope_stack[1:])}.{name}" if len(scope_stack) > 1 else name
                symbols.append(self._build_symbol(node, name, "function", sym_fqn))

                scope_stack.append(name)
                for child in node.children:
                    self._walk_ast(child, code_bytes, symbols, sub_graph, scope_stack)
                scope_stack.pop()

                if scope_pushed:
                    scope_stack.pop()
                return

        # 3. 识别顶层全局常量/变量 (Constant)
        elif node_type in self.GLOBAL_DEF:
            if len(scope_stack) == 1:
                var_name = self._extract_pure_id(node, code_bytes)
                if var_name and var_name.lower() not in self.NOISE_VARIABLES:
                    if var_name.isupper() or len(var_name) > 4:
                        var_fqn = f"{scope_stack[0]}::{var_name}"
                        sub_graph.add_node(
                            var_fqn, type="variable", name=var_name, level="variable"
                        )
                        sub_graph.add_edge(scope_stack[0], var_fqn, relation="contains")

                        # 符号大纲
                        if not any(s["fully_qualified_name"] == var_name for s in symbols):
                            symbols.append(self._build_symbol(node, var_name, "constant", var_name))

        # 4. 识别函数内调用 (Calls)
        elif node_type in self.FUNC_CALL:
            if len(scope_stack) > 1:
                called_name = self._extract_call_name(node, code_bytes)
                if called_name:
                    caller_fqn = "::".join(scope_stack)
                    target_id_hint = f"{scope_stack[0]}:{called_name}"
                    sub_graph.add_edge(caller_fqn, target_id_hint, relation="calls")

        for child in node.children:
            self._walk_ast(child, code_bytes, symbols, sub_graph, scope_stack)

        if scope_pushed:
            scope_stack.pop()

    def _extract_class_members(self, class_node: tree_sitter.Node, code_bytes: bytes, symbols: list, sub_graph: nx.DiGraph, scope_stack: list):
        for child in class_node.children:
            if child.type in ["block", "declaration_list"]:
                for sub in child.children:
                    if sub.type in ["assignment", "field_declaration"]:
                        attr_name = self._extract_pure_id(sub, code_bytes)
                        if attr_name and attr_name.lower() not in self.NOISE_VARIABLES:
                            attr_fqn = "::".join(scope_stack + [attr_name])
                            if not sub_graph.has_node(attr_fqn):
                                sub_graph.add_node(attr_fqn, type="property", name=attr_name, level="property")
                                class_fqn = "::".join(scope_stack)
                                sub_graph.add_edge(class_fqn, attr_fqn, relation="contains")

                            sym_fqn = f"{'.'.join(scope_stack[1:])}.{attr_name}"
                            symbols.append(self._build_symbol(sub, attr_name, "constant", sym_fqn))

    def _extract_name(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        try:
            name_node = node.child_by_field_name("name")
            if name_node:
                return code_bytes[name_node.start_byte: name_node.end_byte].decode("utf-8", errors="ignore")
            for child in node.children:
                if child.type in self.NAME_DEC:
                    return code_bytes[child.start_byte: child.end_byte].decode("utf-8", errors="ignore")
        except Exception as e:
            print(e)
            pass
        return ""

    def _extract_pure_id(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        try:
            left_node = node.child_by_field_name("left") or node.child_by_field_name("name")
            if not left_node and node.child_count > 0:
                left_node = node.child(0)
            if left_node:
                if left_node.type not in self.NAME_DEC:
                    return ""
                name_str = code_bytes[left_node.start_byte: left_node.end_byte].decode("utf-8", errors="ignore").strip()
                if name_str.isidentifier() and not name_str.startswith("_"):
                    return name_str
        except Exception as e:
            print(e)
            pass
        return ""

    def _extract_call_name(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        try:
            if node.child_count > 0:
                first_child = node.child(0)
                if first_child and first_child.type in self.NAME_DEC:
                    return code_bytes[first_child.start_byte: first_child.end_byte].decode("utf-8", errors="ignore")
        except Exception as e:
            print(e)
            pass
        return ""

    def _build_symbol(self, node: tree_sitter.Node, name: str, kind: str, fqn: str) -> dict:
        start_point = node.start_point
        end_point = node.end_point
        return {
            "name": name,
            "kind": kind,
            "fully_qualified_name": fqn,
            "extent_utf16": {
                "start": {"line_number": start_point[0] + 1, "utf16_col": start_point[1]},
                "end": {"line_number": end_point[0] + 1, "utf16_col": end_point[1]},
            },
        }