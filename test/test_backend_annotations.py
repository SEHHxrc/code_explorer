"""后端文档字符串和函数类型标注的维护契约。"""

import ast
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend" / "app"


class BackendAnnotationContractTests(unittest.TestCase):
    """防止新增后端定义时遗漏职责说明、形参类型或返回类型。"""

    def test_backend_definitions_are_documented_and_typed(self) -> None:
        """扫描后端 AST，输出所有缺少说明或类型标注的定义位置。"""
        problems: list[str] = []
        for path in sorted(BACKEND_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            relative = path.relative_to(BACKEND_ROOT.parent.parent)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and ast.get_docstring(node, clean=False) is None:
                    problems.append(f"{relative}:{node.lineno} class {node.name} missing docstring")
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if ast.get_docstring(node, clean=False) is None:
                    problems.append(f"{relative}:{node.lineno} function {node.name} missing docstring")
                arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                if node.args.vararg:
                    arguments.append(node.args.vararg)
                if node.args.kwarg:
                    arguments.append(node.args.kwarg)
                for argument in arguments:
                    if argument.arg not in {"self", "cls"} and argument.annotation is None:
                        problems.append(
                            f"{relative}:{node.lineno} {node.name} argument {argument.arg} missing type"
                        )
                if node.returns is None:
                    problems.append(f"{relative}:{node.lineno} {node.name} missing return type")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()