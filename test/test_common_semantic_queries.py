"""公共调用查询的身份、位置、歧义和兼容边界；不调用原生工具或模型。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.security_analysis.scanner import SecurityScanner
from backend.app.services.semantic_index import (
    CallSiteFact, CallTransitionIndex, CallTransitionView, ResolvedTargetFact,
    SemanticCallSiteView, SemanticIndex, SemanticIndexArtifact, SemanticLocation,
)


class CommonSemanticQueryTests(unittest.TestCase):
    """位置不是名字解析；多个匹配和未解析调用必须完整保留。"""

    def fact(self, name: str, column: int, *, caller: str = "app.ts::run") -> CallSiteFact:
        """构造同一行不同列的调用，使用生产身份算法并保留未知目标。"""
        location = SemanticLocation(path="src/app.ts", line=4, column=column, end_line=4, end_column=column + 5,
                                    location_id=ProgramIdentity.location_id("src/app.ts", 4, column, 4, column + 5))
        return CallSiteFact(callsite_id=ProgramIdentity.callsite_for_location(location.location_id), caller_symbol=caller,
                            language="typescript", name=name, location=location, unresolved_reason="unknown_target")

    def test_position_query_preserves_ambiguity_and_never_guesses_by_name(self) -> None:
        first, second = self.fact("consume", 3), self.fact("consume", 20, caller="app.ts::nested")
        artifact = SemanticIndexArtifact(callsites={item.callsite_id: item for item in (second, first)})
        before = artifact.model_dump()
        index = SemanticIndex(artifact)
        self.assertIsInstance(index, SemanticCallSiteView)
        self.assertEqual((first, second), index.calls_at(SemanticLocation(path="./src\\app.ts", line=4)))
        self.assertEqual((first,), index.calls_at(first.location))
        self.assertEqual((second,), index.calls_at(SemanticLocation(path="src/app.ts", line=4), caller_symbol=second.caller_symbol))
        self.assertEqual((), index.calls_at(SemanticLocation(path="src/app.ts", line=4, column=5)))
        self.assertEqual((), index.calls_at(SemanticLocation(path="src/app.ts", line=4, column=3, end_column=100)))
        self.assertEqual((), index.calls_at(SemanticLocation(path="src/app.ts")))
        self.assertEqual((), index.calls_at(SemanticLocation(path="other.ts", line=4)))
        self.assertEqual(artifact.model_dump(), before)

    def test_callsite_query_keeps_unresolved_reason_and_conflicting_location(self) -> None:
        fact = self.fact("missing", 2)
        index = SemanticIndex(SemanticIndexArtifact(callsites={fact.callsite_id: fact}))
        self.assertEqual("unknown_target", index.callsite(fact.callsite_id).unresolved_reason)
        self.assertIsNone(index.callsite("not-present"))
        location = fact.location.model_copy(update={"location_id": "other-location"})
        self.assertEqual((), index.calls_at(location))

    def test_callsite_transitions_preserve_all_targets_but_not_instantiation(self) -> None:
        fact = self.fact("dispatch", 2)
        fact.targets = [ResolvedTargetFact(edge_id=name, target_symbol=name, relation=relation, certainty="may", confidence="high")
                        for name, relation in (("z", "calls"), ("a", "calls"), ("Class", "instantiates"))]
        fact.truncated = True
        calls = CallTransitionIndex.from_semantic_index(SemanticIndexArtifact(callsites={fact.callsite_id: fact}))
        self.assertIsInstance(calls, CallTransitionView)
        items = calls.for_callsite(fact.caller_symbol, fact.callsite_id)
        self.assertEqual(["a", "z"], [item.target for item in items])
        self.assertTrue(all(item.truncated and item.certainty == "may" and item.confidence == "high" for item in items))
        self.assertEqual(items, calls.for_source(fact.caller_symbol))
        self.assertEqual((items[0],), calls.for_target("a"))
        self.assertEqual((), calls.for_callsite("other-caller", fact.callsite_id))

    def test_authoritative_unresolved_semantic_call_does_not_merge_old_graph_targets(self) -> None:
        fact = self.fact("missing", 2)
        graph = {"links": [{"relation": "calls", "source": fact.caller_symbol, "target": "old_guess",
                            "callsite_id": fact.callsite_id, "confidence": "high", "target_certainty": "must"}]}
        semantic = SemanticIndexArtifact(callsites={fact.callsite_id: fact})
        calls = CallTransitionIndex.from_sources(semantic_index=semantic, dependency_graph=graph)
        self.assertEqual((), calls.for_source(fact.caller_symbol))
        legacy = CallTransitionIndex.from_sources(semantic_index={}, dependency_graph=graph)
        transition = legacy.for_callsite(fact.caller_symbol, fact.callsite_id)[0]
        self.assertEqual("old_guess", transition.target)
        self.assertEqual("must", transition.certainty)
        self.assertEqual(transition, CallTransitionIndex.from_dependency_graph(graph).for_source(fact.caller_symbol)[0])

    def test_scanner_builds_one_shared_index_for_multiple_language_groups(self) -> None:
        sources = {
            "app.js": "const express=require('express'); const app=express(); app.get('/x',(req,res)=>res.send(req.query.x));",
            "other.ts": "import type {Request} from 'express'; export function handle(req:Request){return req.query.x;}",
        }
        graph = {"nodes": [{"kind": "module", "file": name, "lang": "typescript" if name.endswith(".ts") else "javascript"}
                           for name in sources], "links": []}
        with tempfile.TemporaryDirectory() as directory:
            for name, source in sources.items():
                Path(directory, name).write_text(source, encoding="utf8")
            with patch.object(CallTransitionIndex, "from_dependency_graph", wraps=CallTransitionIndex.from_dependency_graph) as build:
                scan = SecurityScanner().scan(directory, dependency_graph=graph)
            self.assertEqual(2, scan.files_scanned)
            self.assertEqual(1, build.call_count)


if __name__ == "__main__":
    unittest.main()
