import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import networkx as nx
import tree_sitter
from tree_sitter_language_pack import get_parser


def _build_symbol(node: tree_sitter.Node, name: str, kind: str, fqn: str) -> dict:
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
    CLS_DEF = ["class_definition", "class_declaration", "struct_item", "type_specifier", "type_declaration"]

    CLS_CONTAIN = ["block", "declaration_list", "field_declaration_list", "class_body"]

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

    CALLEE = ["attribute", "member_expression", "field_expression"]

    # 名字与标识符常量复用
    NAME_DEC = ["identifier", "name", "type_identifier", "field_identifier"]

    # 属性/字段定义
    PROPERTY_DEF = ["assignment", "field_declaration", "property_definition"]

    # 导入包定义
    IMPORT_DEF = ["import_from_statement", "import_statement", "import_declaration", "preproc_include", "aliased_import"]

    # 包名识别
    IMPORT_NAME = ["name", "dotted_name",]

    # 降噪黑名单
    NOISE_VARIABLES = [
        "i", "j", "k", "x", "y", "z", "e", "ex", "f", "fp",
        "c", "n", "res", "result", "val", "value", "key",
        "item", "items", "data", "tmp", "temp", "ret", "args",
        "kwargs", "self", "cls",
    ]

    # 内置函数
    UNIVERSAL_BUILTINS = [

    ]

    def __init__(self, project_root: str, max_workers: int = 4):
        self.project_root = project_root
        self.max_workers = max_workers
        self.global_graph = nx.DiGraph()
        self.file_symbols_map = {}

        self.global_index = {"imports":{}, "inherits_todo": []}  # 第二轮建立跨文件连接用

        self.parsed_files_count = 0
        self.total_files_count = 0

    def run_full_analysis(self) -> dict:
        """执行全项目统一分析，返回 { "file_symbols": {...}, "dependency_graph": {...} }"""
        target_files = []
        # 层次优先遍历项目目录下所有文件（os.walk为深度优先）
        for root, _, files in os.walk(self.project_root):
            for file in files:
                _, ext = os.path.splitext(file)
                if ext.lower() in self.EXT_MAP:
                    target_files.append(os.path.join(root, file))

        self.total_files_count = len(target_files)
        if self.total_files_count == 0:
            return {"file_symbols": {}, "dependency_graph": nx.node_link_data(self.global_graph)}

        # 线程池并发处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._process_single_file, filepath): filepath
                for filepath in target_files
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
        """获取当前处理进度"""
        return {"total_files": self.total_files_count, "parsed_files": self.parsed_files_count}

    def _process_single_file(self, full_path) -> tuple:
        """单个文件独立解析核心：同时产出 Symbols 和 SubGraph 边/节点"""
        # 项目本身为根目录的文件路径
        rel_path = os.path.relpath(full_path, self.project_root).replace("\\", "/")
        _, ext = os.path.splitext(full_path)
        lang_name = self.EXT_MAP[ext.lower()]

        symbols = []
        sub_graph = nx.DiGraph()

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                code_bytes = f.read().encode("utf-8")

            parser = get_parser(lang_name)
            tree = parser.parse(code_bytes)

            # 注册文件节点
            sub_graph.add_node(rel_path, type="module", lang=lang_name, level="module")

            # 递归遍历 AST，复用同一套作用域逻辑
            scope_stack = [rel_path]
            field_stack = [tree.root_node.type]
            self._walk_ast(tree.root_node, code_bytes, symbols, sub_graph, scope_stack, field_stack)

        except Exception as e:
            print(f"[Warning] Parse error in {rel_path}: {e}")
            traceback.print_exc()

        return rel_path, symbols, list(sub_graph.nodes(data=True)), list(sub_graph.edges(data=True)),

    def _walk_ast(self,node: tree_sitter.Node, code_bytes: bytes, symbols: list, sub_graph: nx.DiGraph, scope_stack: list, field_stack: list):
        if not node:
            return

        current_file = scope_stack[0]
        node_type = node.type
        scope_pushed = False
        field_pushed = False

        # 第一类，定义域
        # 0. 识别 import
        if node_type in self.IMPORT_DEF or self.IMPORT_NAME:
            if current_file not in self.global_index["imports"]:
                self.global_index["imports"][current_file] = {}
            field_stack.append(node_type)
            field_pushed = True

        # 1. 类或结构体 (Class & Struct)
        elif node_type in self.CLS_DEF:
            field_stack.append(node_type)
            field_pushed = True

        # 2. 函数或方法 (Function & Method)
        elif node_type in self.FUNC_DEF:
            field_stack.append(node_type)
            field_pushed = True

        # 3. 顶层全局常量/变量 (Constant)
        elif node_type in self.GLOBAL_DEF:
            field_stack.append(node_type)
            field_pushed = True

        # 4. 结构体/类内部属性
        elif node_type in self.PROPERTY_DEF:
            field_stack.append(node_type)
            field_pushed = True

        # 4. 函数内调用 (Calls)
        elif node_type in self.FUNC_CALL:
            field_stack.append(node_type)
            field_pushed = True

        # 第二类，域内
        if node_type in self.IMPORT_NAME:
            # python
            if node_type == 'dotted_name':
                real_name = code_bytes[node.start_byte: node.end_byte].decode("utf-8").strip()
                # import xx
                if field_stack[:-1] == 'import_statement':
                    self.global_index["imports"][current_file].append({real_name:{'from_module': real_name, 'real_name': real_name}})
                # import xx (as ...)
                elif field_stack[:-1] == 'aliased_import':
                    pass
            # from ... import *
            elif node_type == 'wildcard_import':
                pass
            # as xx
            # elif node_type == 'identifier':
            #     use_name = code_bytes[node.start_byte: node.end_byte].decode("utf-8").strip()
            #     self.global_index["imports"][current_file].append({use_name:{'from_module': use_name, 'real_name': real_name}})

        elif node_type in self.NAME_DEC:
            name = code_bytes[node.start_byte: node.end_byte].decode("utf-8").strip()
            scope_stack.append(name)
            scope_pushed = True

        elif node_type in self.CALLEE:
            name = code_bytes[node.start_byte: node.end_byte].decode("utf-8").strip()
            scope_stack.append(name)
            scope_pushed = True

        # 统一的子节点迭代
        for child in node.children:
            self._walk_ast(child, code_bytes, symbols, sub_graph, scope_stack, field_stack)

        # 恢复作用域栈
        if scope_pushed:
            kind = self._combine(scope_stack, field_stack[-1], sub_graph)
            if kind:
                fqn = '.'.join(scope_stack[1:])
                _build_symbol(node, scope_stack[-1], kind, fqn)
            scope_stack.pop()

        # 恢复类型栈
        if field_pushed:
            field_stack.pop()

    def _combine(self, scope: list[str], relation: str, graph: nx.DiGraph) -> str | None:
        fqn = '::'.join(scope)
        parent_fqn = '::'.join(scope[:-1])
        name = scope[-1]
        if relation in self.CLS_DEF:
            graph.add_node(fqn, type="class", name=name, level="class")
            graph.add_edge(parent_fqn, fqn, relation='contains')
            return 'class'
        elif relation in self.FUNC_DEF:
            parent_type = graph.nodes.get(parent_fqn, {}).get("type", "module")
            level_type = "method" if parent_type == "class" else "function"
            graph.add_node(fqn, type=level_type, name=name, level=level_type)
            graph.add_edge(parent_fqn, fqn, relation="contains")
            return level_type
        elif relation in self.GLOBAL_DEF:
            if len(scope) == 2:  # 确保顶层
                graph.add_node(fqn, type="variable", name=name, level="variable")
                graph.add_edge(parent_fqn, fqn, relation="contains")
                return 'constant'
        elif relation in self.PROPERTY_DEF:
            if len(scope) > 2:  # 确保非顶层
                graph.add_node(fqn, type="property", name=name, level="property")
                graph.add_edge(parent_fqn, fqn, relation="contains")
                return 'property'
        elif relation in self.FUNC_CALL:
            graph.add_edge(parent_fqn, fqn, relation="calls")
        return None
