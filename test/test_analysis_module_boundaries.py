"""分析模块依赖方向和旧入口兼容契约，不以目录结构替代运行回归。"""

import ast
from pathlib import Path
import unittest


SERVICES = Path(__file__).resolve().parents[1] / "backend" / "app" / "services"


class AnalysisModuleBoundaryTests(unittest.TestCase):
    """保护稳定入口及基础设施的职责隔离。"""

    def test_old_entries_reexport_the_same_implementations(self) -> None:
        """兼容入口不保留并行算法或子类，身份与新入口完全相同。"""
        from backend.app.core.progress import report_progress
        from backend.app.services.project_analysis.progress import report_progress as old_progress
        from backend.app.services.language_backends import GoSemanticResolver, TypeScriptSemanticResolver
        from backend.app.services.semantic_index.go import GoSemanticResolver as old_go
        from backend.app.services.semantic_index.typescript import TypeScriptSemanticResolver as old_ts
        from backend.app.services.value_binding.access_paths import AccessPath
        from backend.app.services.program_graph.frontends.access_paths import AccessPath as old_path
        self.assertIs(report_progress, old_progress)
        self.assertIs(GoSemanticResolver, old_go)
        self.assertIs(TypeScriptSemanticResolver, old_ts)
        self.assertIs(AccessPath, old_path)
        from backend.app.services.security_analysis.frontends import GoSecurityFrontend as old_adapter
        from backend.app.services.security_analysis.language_adapters import GoSecurityFrontend
        from backend.app.services.security_analysis.frontends.treesitter import TreeSitterIRCollector as old_collector
        from backend.app.services.security_analysis.language_adapters.treesitter import TreeSitterIRCollector
        self.assertIs(GoSecurityFrontend, old_adapter)
        self.assertIs(TreeSitterIRCollector, old_collector)
        from backend.app.services.semantic_index import CallTransitionIndex
        from backend.app.services.security_analysis.flow_analysis.call_graph import CallTransitionIndex as old_calls
        self.assertIs(CallTransitionIndex, old_calls)

    def test_common_call_query_does_not_depend_on_security_solver(self) -> None:
        """公共查询不得反向依赖安全域；扫描与参数角色转发不能导入传播求解层。"""
        for path in (SERVICES / "semantic_index").glob("*.py"):
            self.assertNotIn("services.security_analysis", path.read_text(encoding="utf-8-sig"))
        for name in ("scanner.py", "parameter_apis.py"):
            self.assertNotIn("flow_analysis", (SERVICES / "security_analysis" / name).read_text(encoding="utf-8-sig"))
        module = ast.parse((SERVICES / "security_analysis" / "flow_analysis" / "call_graph.py").read_text(encoding="utf-8-sig"))
        self.assertEqual(["ProgramArgumentBinder"], [node.name for node in module.body if isinstance(node, ast.ClassDef)])

    def test_old_security_frontend_directory_has_no_algorithm(self) -> None:
        """旧目录只允许文档和再导出，禁止把旧版本实现重新放回兼容路径。"""
        for path in (SERVICES / "security_analysis" / "frontends").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                                 for node in ast.walk(tree)), str(path))

    def test_analysis_foundations_do_not_depend_on_application_orchestration(self) -> None:
        """规则、图、语义和语法不得反向调用项目事务、模型、Agent 或 HTTP。"""
        forbidden = ("backend.app.services.projects", "backend.app.services.project_analysis", "backend.app.agents",
                     "backend.app.experiments", "backend.app.llm", "backend.app.api", "backend.app.repositories")
        for directory in ("syntax_analysis", "value_binding", "language_backends", "semantic_index", "program_graph", "security_analysis", "dependency_analyzer"):
            for path in (SERVICES / directory).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                for node in ast.walk(tree):
                    modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                               else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                    for module in modules:
                        self.assertFalse(any(module == item or module.startswith(item + ".") for item in forbidden),
                                         f"{path.relative_to(SERVICES)} 不应依赖 {module}")

    def test_semantic_index_core_does_not_start_native_processes(self) -> None:
        """语义事实核心不装配进程；只有明确命名的旧路径兼容文件可转出后端实现。"""
        facades = {"go.py", "go_environment.py", "typescript.py", "wire.py", "worker.py"}
        for path in (SERVICES / "semantic_index").glob("*.py"):
            if path.name not in facades:
                self.assertNotIn("language_backends", path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
