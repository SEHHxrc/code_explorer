"""语义层性能机制的等价与失效边界回归，不依赖模型或网络。"""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import networkx as nx
from pydantic import ValidationError

from backend.app.services.semantic_index.contracts import SemanticLocation
from backend.app.services.semantic_index.resolution import DeclarationBinding, DeclarationSymbolIndex, ProjectSemanticResult
from backend.app.services.language_backends.typescript import TOOL_DIRECTORY, TypeScriptSemanticResolver
from backend.app.services.semantic_index import DependencyAnalysisSemanticProvider
from backend.app.services.semantic_index import provider as provider_module
from backend.app.services.dependency_analyzer.models import Reference
from test import test_typescript_semantics as fixtures


class SharedSemanticIndexTests(unittest.TestCase):
    """Go/Java 等后端也可复用声明查询和性能记录，不带 JS 私有语法。"""

    def test_declaration_lookup_retains_ambiguity(self) -> None:
        """重复同一符号不算歧义，不同符号冲突后不能被后一次写入恢复。"""
        key = ("Example.java", "run", "method", 2, 4)
        index = DeclarationSymbolIndex([("A::run", key), ("A::run", key)])
        declaration = DeclarationBinding(location=SemanticLocation(path=key[0], line=2, end_line=4),
                                         name="run", kind="method", has_body=True)
        self.assertEqual("A::run", index.symbol_for(declaration))
        ambiguous = DeclarationSymbolIndex([("A::run", key), ("B::run", key), ("A::run", key)])
        self.assertIsNone(ambiguous.symbol_for(declaration))
        self.assertIsNone(index.symbol_for(declaration.model_copy(update={"name": "other"})))

    def test_metrics_are_optional_backend_neutral_metadata(self) -> None:
        """旧结果无计时仍可读，新后端可按相同接口记录而不改变事实模型。"""
        old = ProjectSemanticResult(backend="go", status="completed")
        self.assertEqual({}, old.timings_ms)
        new = old.model_copy(update={"timings_ms": {"program_setup": 5.5}, "cache_stats": {"declaration_hits": 3}})
        restored = ProjectSemanticResult.model_validate_json(new.model_dump_json())
        self.assertEqual(new.summary(), restored.summary())

    def test_batch_boundaries_do_not_change_facts(self) -> None:
        """单条与跨多批的完整产物相同，批次大小有界，不抽样或丢失调用。"""
        references = [Reference(file="a.py", from_fqn="a.py::run", kind="call", name="target",
                                line=index + 1, column=1, end_line=index + 1, end_column=9, lang="python")
                      for index in range(33)]
        service = DependencyAnalysisSemanticProvider()
        options = dict(definitions={}, references=references, variable_types={}, graph=nx.MultiDiGraph(), unresolved=[])
        service.CALLSITE_BATCH_SIZE = 1
        expected = service.build(**options).model_dump()
        adapter = provider_module._CALLSITE_BATCH
        sizes = []

        def validate(batch: dict) -> dict:
            """记录瞬时批次大小，仍交给真实 Pydantic 模型校验。"""
            sizes.append(len(batch))
            return adapter.validate_python(batch)

        service.CALLSITE_BATCH_SIZE = 7
        with patch.object(provider_module, "_CALLSITE_BATCH") as instrumented:
            instrumented.validate_python.side_effect = validate
            actual = service.build(**options).model_dump()
        self.assertEqual(expected, actual)
        self.assertEqual([7, 7, 7, 7, 5], sizes)
        self.assertEqual(33, len(actual["callsites"]))

    def test_batching_does_not_bypass_model_validation(self) -> None:
        """错误字段依然失败，不能把批处理优化变成 model_construct 信任捷径。"""
        with self.assertRaises(ValidationError):
            DependencyAnalysisSemanticProvider().build(
                definitions={}, references=[], variable_types={}, graph=nx.MultiDiGraph(),
                unresolved=[{"callsite_id": "bad", "file": "a.py", "fallback_reason": {"invalid": True}}],
            )


@unittest.skipUnless(shutil.which("node") and (TOOL_DIRECTORY / "node_modules/typescript/lib/typescript.js").is_file(),
                     "需先准备 TypeScript 工具依赖")
class SemanticCacheTests(unittest.TestCase):
    """真实工作进程的任务级缓存不缩小扫描，不跨请求留下旧源码。"""

    def test_repeated_declarations_hit_caches_without_dropping_calls(self) -> None:
        """相同函数的多处调用仍各有定位，缓存仅复用声明计算。"""
        result = fixtures.TypeScriptSemanticTests().analyze({"a.ts":
            "function helper(value:string){return value;}\n"
            "function run(value:string){helper(value); helper(value); return helper(value);}\n"})
        index = result["semantic_index"]
        calls = [item for item in index["callsites"].values() if item["name"] == "helper"]
        self.assertEqual(3, len(calls))
        self.assertEqual(3, len({item["callsite_id"] for item in calls}))
        self.assertTrue(all(item["targets"][0]["target_symbol"] == "a.ts::helper" for item in calls))
        metrics = index["backend_runs"][0]
        self.assertGreater(metrics["cache_stats"]["declaration_hits"], 0)
        self.assertGreater(metrics["cache_stats"]["declaration_id_hits"], 0)
        self.assertTrue({"worker_wall", "json_load", "protocol_decode", "resolver_wall"}.issubset(metrics["timings_ms"]))

    def test_cache_lifetime_is_one_request(self) -> None:
        """同一个 Python 解析器重用后必须重新读文件，不能返回上一快照的声明。"""
        resolver = TypeScriptSemanticResolver()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "a.ts")
            names = []
            for name in ("before", "after"):
                target.write_text(f"function {name}(){{}}\nfunction run(){{{name}();}}\n", encoding="utf8")
                result = resolver.resolve(directory, file_paths=["a.ts"], max_file_bytes=2000000)
                names.append({declaration.name for call in result.calls for declaration in call.declarations})
        self.assertEqual([{"before"}, {"after"}], names)

    def test_nearest_config_cache_preserves_child_overrides(self) -> None:
        """父目录已命中后，子目录自己的路径映射仍优先，不串工程配置。"""
        result = fixtures.TypeScriptSemanticTests().analyze({
            "tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"@target":["lib/one"]}}}',
            "nested/tsconfig.json": '{"compilerOptions":{"baseUrl":"..","paths":{"@target":["lib/two"]}}}',
            "lib/one.ts": "export function f(){return 1;}\n",
            "lib/two.ts": "export function f(){return 2;}\n",
            "main.ts": "import {f as target} from '@target';\nfunction run(){return target();}\n",
            "nested/main.ts": "import {f as target} from '@target';\nfunction run(){return target();}\n",
        })
        calls = {item["location"]["path"]: item for item in result["semantic_index"]["callsites"].values() if item["name"] == "target"}
        self.assertEqual("lib/one.ts::f", calls["main.ts"]["targets"][0]["target_symbol"])
        self.assertEqual("lib/two.ts::f", calls["nested/main.ts"]["targets"][0]["target_symbol"])


if __name__ == "__main__":
    unittest.main()
