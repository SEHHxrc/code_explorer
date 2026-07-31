import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import networkx as nx
import tree_sitter
from tree_sitter_language_pack import get_parser


class UniversalDependencyAnalyzer:
    """支持多线程文件级并发、多语言、多层级的静态依赖分析引擎"""

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

    CLS_DEF = [
        "class_definition",  # Python, JS, C++
        "class_declaration",  # Java
        "struct_item",  # Rust
        "type_specifier",
    ]

    CLS_NAME_DEC = ["identifier", "name", "type_identifier"]

    FUNC_NAME_DEC = [
        "identifier",
        "name",
        "field_identifier",
        "field_expression",
        "scoped_identifier",
        "attribute",
    ]

    FUNC_DEF = [
        "function_definition",  # Python, JS, C/C++
        "method_definition",  # JS, Python 某些语法
        "method_declaration",  # Java
        "function_item",  # Rust
        "function_declaration",  # Go
    ]

    FUNC_CALL = ["call_expression", "call", "method_invocation"]

    def __init__(self, project_root: str, max_workers: int = 4):
        self.project_root = project_root
        self.graph = nx.DiGraph()
        self.max_workers = max_workers  # 并发线程数

        # 进度追踪属性
        self.parsed_files_count = 0
        self.total_files_count = 0

    def analyze(self) -> dict:
        target_files = []
        for root, _, files in os.walk(self.project_root):
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in self.EXT_MAP:
                    target_files.append((root, file))

        self.total_files_count = len(target_files)
        if self.total_files_count == 0:
            return nx.node_link_data(self.graph)

        # 使用线程池并发解析各个文件
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._parse_single_file_safely, root, file): file
                for root, file in target_files
            }

            for future in as_completed(futures):
                try:
                    # 每个文件独立返回它自己解析出来的子图 (SubGraph)
                    sub_nodes, sub_edges = future.result()
                    # 在主线程中安全合并到全局大图中
                    self.graph.add_nodes_from(sub_nodes)
                    self.graph.add_edges_from(sub_edges)
                except Exception as e:
                    print(f"[Error] File analysis task failed: {e}")
                finally:
                    self.parsed_files_count += 1

        return nx.node_link_data(self.graph)

    def get_progress(self) -> dict:
        return {
            "total_files": self.total_files_count,
            "parsed_files": self.parsed_files_count,
        }

    def _parse_single_file_safely(self, root: str, file: str) -> tuple:
        """独立线程执行函数：解析单个文件，返回 (nodes, edges) 列表，保证线程安全"""
        sub_graph = nx.DiGraph()
        full_path = os.path.join(root, file)
        rel_path = os.path.relpath(full_path, self.project_root)
        _, ext = os.path.splitext(file)
        lang_name = self.EXT_MAP[ext.lower()]

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                code_bytes = f.read().encode("utf-8")

            parser = get_parser(lang_name)
            tree = parser.parse(code_bytes)

            # 1. 注册模块/文件节点
            sub_graph.add_node(rel_path, type="module", lang=lang_name, level="module")

            # 在当前线程的私有作用域中递归遍历
            scope_stack = [rel_path]
            self._walk_ast_to_subgraph(tree.root_node, code_bytes, sub_graph, scope_stack)

        except Exception as e:
            print(f"[Warning] Parse error in {rel_path}: {e}")

        return list(sub_graph.nodes(data=True)), list(sub_graph.edges(data=True))

    def _walk_ast_to_subgraph(
            self, node: tree_sitter.Node, code_bytes: bytes, sub_graph: nx.DiGraph, scope_stack: list
    ):
        """独立的 AST 遍历逻辑，直接向私有 sub_graph 中写入节点和边"""
        node_type = node.type
        scope_pushed = False

        # ----------------------------------------------------
        # 1. 识别类或结构体 (Class / Struct)
        # ----------------------------------------------------
        if node_type in self.CLS_DEF:
            class_name = self._extract_name_by_field(node, code_bytes)
            if class_name:
                scope_stack.append(class_name)
                scope_pushed = True
                class_fqn = "::".join(scope_stack)
                sub_graph.add_node(class_fqn, type="class", name=class_name, level="class")
                parent_module = scope_stack[0]
                sub_graph.add_edge(parent_module, class_fqn, relation="contains")

        # ----------------------------------------------------
        # 2. 识别函数或方法 (Function / Method)
        # ----------------------------------------------------
        elif node_type in self.FUNC_DEF:
            func_name = self._extract_name_by_field(node, code_bytes)
            if func_name:
                is_global_func = len(scope_stack) == 1
                if is_global_func:
                    fqn = f"{scope_stack[0]}::{func_name}"
                    sub_graph.add_node(fqn, type="function", name=func_name, level="function")
                    sub_graph.add_edge(scope_stack[0], fqn, relation="contains")
                else:
                    fqn = "::".join(scope_stack + [func_name])
                    sub_graph.add_node(fqn, type="method", name=func_name, level="method")
                    parent_class = "::".join(scope_stack)
                    sub_graph.add_edge(parent_class, fqn, relation="contains")

                scope_stack.append(func_name)
                for child in node.children:
                    self._walk_ast_to_subgraph(child, code_bytes, sub_graph, scope_stack)
                scope_stack.pop()

                if scope_pushed:
                    scope_stack.pop()
                return

        # ----------------------------------------------------
        # 3. 识别函数内部的具体调用 (Calls)
        # ----------------------------------------------------
        elif node_type in self.FUNC_CALL:
            if len(scope_stack) > 1:
                called_name = self._extract_call_name(node, code_bytes)
                if called_name:
                    caller_fqn = "::".join(scope_stack)
                    target_id_hint = f"{scope_stack[0]}:{called_name}"
                    sub_graph.add_edge(caller_fqn, target_id_hint, relation="calls")

        for child in node.children:
            self._walk_ast_to_subgraph(child, code_bytes, sub_graph, scope_stack)

        if scope_pushed:
            scope_stack.pop()

    def _extract_name_by_field(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        """提取定义块的名字（类名或函数名）"""
        name_node = node.child_by_field_name("name")
        if name_node:
            return code_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="ignore")
        for child in node.children:
            if child.type in self.CLS_NAME_DEC:
                return code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
        return ""

    def _extract_call_name(self, node: tree_sitter.Node, code_bytes: bytes) -> str:
        """提取被调用函数的名字"""
        first_child = node.child(0)
        if first_child:
            if first_child.type in self.FUNC_NAME_DEC:
                return code_bytes[first_child.start_byte : first_child.end_byte].decode("utf-8", errors="ignore")
        return ""