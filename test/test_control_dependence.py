"""公共控制依赖和路径条件核验：真实多语言前端、互斥路线、版本更新与未知边界。"""

import tempfile
import unittest
from pathlib import Path

from backend.app.services.program_graph import ProgramGraphService, ProgramGraphArtifact, ProgramGraphEdge
from backend.app.services.program_graph.conditions import ConditionExpression
from backend.app.services.program_graph.path_conditions import PathConditionQuery
from backend.app.services.program_graph.passes.control_dependence import ControlDependencePass
from backend.app.services.program_graph.passes.reaching_definitions import ReachingDefinitionsPass
from backend.app.services.security_analysis.evidence_query import SecurityEvidenceCatalog
from test import test_instance_fields as fixtures


class _GraphFixture(unittest.TestCase):
    """生产图构建辅助，不继承已有测试类，避免重复收集同一批测试。"""

    def graph(self, source: str, filename: str = "app.py") -> ProgramGraphArtifact:
        """使用生产服务构建图，不以手写 AST 替代跨语言验证。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, filename).write_text(source, encoding="utf8")
            artifact = ProgramGraphService().analyze(directory)
        self.assertFalse(artifact.failures, artifact.failures)
        self.assertTrue(artifact.functions)
        return artifact


class ControlDependenceTests(_GraphFixture):
    """控制依赖属于公共 CFG 结构，不应复制成各语言专用求解器。"""

    def test_all_languages_share_dominance_and_conditions(self) -> None:
        """八种已支持语言的纯比较均交给同一 IR 和控制依赖 Pass。"""
        cases = {
            "app.py": "def choose(x):\n if x == 1:\n  return 7\n return 0\n",
            "App.java": "class App { int choose(int x) { if (x == 1) return 7; return 0; } }",
            "app.go": "package app\nfunc choose(x int) int { if x == 1 { return 7 }; return 0 }",
            "app.js": "function choose(x) { if (x === 1) return 7; return 0; }",
            "app.ts": "function choose(x: number): number { if (x === 1) return 7; return 0; }",
            "app.c": "int choose(int x) { if (x == 1) return 7; return 0; }",
            "app.cpp": "int choose(int x) { if (x == 1) return 7; return 0; }",
            "app.rs": "fn choose(x: i32) -> i32 { if x == 1 { return 7; } return 0; }",
        }
        for filename, source in cases.items():
            with self.subTest(filename=filename):
                graph = self.graph(source, filename)
                function = next(iter(graph.functions.values()))
                condition = next(item for item in function.nodes.values() if item.kind == "condition")
                self.assertEqual("compare", condition.condition.kind)
                self.assertEqual("eq", condition.condition.operator)
                self.assertTrue(any(edge.source == condition.node_id and edge.branch == "true"
                                    for edge in function.edges if edge.kind == "control_dependence"))
                target = next(item for item in function.nodes.values() if item.kind == "return" and "7" in item.code)
                proof = PathConditionQuery(timeout_ms=2000).prove(function, [target.node_id])
                self.assertEqual("satisfiable_under_model", proof.status, proof)
                self.assertEqual(filename, proof.conditions[0].location["path"])

    def test_diamond_merge_is_not_control_dependent(self) -> None:
        """分支内赋值受控制，正常合流后的 return 不受该分支控制。"""
        function = next(iter(self.graph("def choose(x):\n if x:\n  y=1\n else:\n  y=2\n return y\n").functions.values()))
        condition = next(item for item in function.nodes.values() if item.kind == "condition")
        merge = next(item for item in function.nodes.values() if item.kind == "return")
        summary = function.control_dependence
        self.assertEqual(merge.node_id, summary.immediate_postdominators[condition.node_id])
        dependencies = [edge for edge in function.edges if edge.kind == "control_dependence"]
        self.assertFalse(any(edge.target == merge.node_id for edge in dependencies))
        self.assertEqual({"true", "false"}, {edge.branch for edge in dependencies})
        rebuilt = ControlDependencePass().apply(function)
        self.assertEqual(function.model_dump(), rebuilt.model_dump())
        invalidated = ReachingDefinitionsPass().apply(function)
        self.assertIsNone(invalidated.control_dependence)

    def test_all_languages_share_scalar_copy_constraints(self) -> None:
        """八种语言的名称复制不能丢失既有约束，不按语言再写求解器。"""
        cases = {
            "app.py": "def choose(x):\n if x==1:\n  y=x\n  if y!=1:\n   return 7\n return 0\n",
            "App.java": "class App { int choose(int x) { if(x==1) { int y=x; if(y!=1) return 7; } return 0; } }",
            "app.go": "package app\nfunc choose(x int) int { if x==1 { y:=x; if y!=1 { return 7 } }; return 0 }",
            "app.js": "function choose(x) { if(x===1) { const y=x; if(y!==1) return 7; } return 0; }",
            "app.ts": "function choose(x: number) { if(x===1) { const y=x; if(y!==1) return 7; } return 0; }",
            "app.c": "int choose(int x) { if(x==1) { int y=x; if(y!=1) return 7; } return 0; }",
            "app.cpp": "int choose(int x) { if(x==1) { int y=x; if(y!=1) return 7; } return 0; }",
            "app.rs": "fn choose(x: i32) -> i32 { if x==1 { let y=x; if y!=1 { return 7; } } return 0; }",
        }
        for filename, source in cases.items():
            with self.subTest(filename=filename):
                function = next(iter(self.graph(source, filename).functions.values()))
                target = next(item for item in function.nodes.values() if item.kind == "return" and "7" in item.code)
                proof = PathConditionQuery(timeout_ms=2000).prove(function, [target.node_id])
                self.assertEqual("infeasible_under_model", proof.status, proof)

    def test_nested_loop_jumps_and_exception_labels_are_preserved(self) -> None:
        """循环、嵌套条件、continue/break 和显式异常共同消费真实 CFG 分支。"""
        function = next(iter(self.graph("def choose(x):\n while x:\n  if x==1:\n   break\n  if x==2:\n   continue\n  raise ValueError()\n return 0\n").functions.values()))
        dependencies = [edge for edge in function.edges if edge.kind == "control_dependence"]
        for kind in ("break", "continue", "throw"):
            target = next(item for item in function.nodes.values() if item.kind == kind)
            self.assertTrue(any(edge.target == target.node_id for edge in dependencies))
        self.assertTrue(any(edge.kind == "cfg" and edge.branch == "exception" for edge in function.edges))

    def test_go_and_rust_precondition_loops_use_common_formula(self) -> None:
        """Go 条件 for 与 Rust while 可复用前置谓词，range/迭代与 for-clause 不冒充。"""
        for filename, source in (
            ("app.go", "package app\nfunc choose(x int) int { for x>0 { return 7 }; return 0 }"),
            ("app.rs", "fn choose(x: i32) -> i32 { while x>0 { return 7; } return 0; }"),
        ):
            function = next(iter(self.graph(source, filename).functions.values()))
            condition = next(item for item in function.nodes.values() if item.kind == "condition")
            self.assertEqual("gt", condition.condition.operator)

    def test_early_return_unreachable_and_nontermination_are_explicit(self) -> None:
        """早退后语句依赖 false 分支，不可达语句与无出口环不能冒充真实必经出口。"""
        function = next(iter(self.graph("def choose(x):\n if x:\n  return 1\n return 0\n dead=3\n").functions.values()))
        condition = next(item for item in function.nodes.values() if item.kind == "condition")
        target = next(item for item in function.nodes.values() if item.code == "return 0")
        dead = next(item for item in function.nodes.values() if item.code == "dead=3")
        self.assertIn(dead.node_id, function.control_dependence.unreachable_nodes)
        self.assertTrue(any(edge.source == condition.node_id and edge.target == target.node_id and edge.branch == "false"
                            for edge in function.edges if edge.kind == "control_dependence"))
        cyclic = function.model_copy(update={"edges": [
            ProgramGraphEdge(edge_id="entry", source=function.entry_node_id, target=condition.node_id, kind="cfg"),
            ProgramGraphEdge(edge_id="loop", source=condition.node_id, target=condition.node_id, kind="cfg", branch="true")],
            "nodes": {key: value for key, value in function.nodes.items() if key in {function.entry_node_id, condition.node_id, function.exit_node_id}}})
        result = ControlDependencePass().apply(cyclic)
        self.assertEqual({function.entry_node_id, condition.node_id}, set(result.control_dependence.non_exit_reaching_nodes))
        self.assertEqual("", result.control_dependence.immediate_postdominators[condition.node_id])


class PathConditionTests(_GraphFixture):
    """路径条件只给模型内结论；原始安全候选不因未证明而消失。"""

    def proof(self, source: str, target: str = "return 7", *, max_states: int = 256):
        """返回真实函数与目标路线核验，使用较宽测试时间避免机器负载造成偶发失败。"""
        function = next(iter(self.graph(source).functions.values()))
        node = next(item for item in function.nodes.values() if item.code == target)
        return function, PathConditionQuery(max_states=max_states, timeout_ms=2000).prove(function, [node.node_id])

    def test_contradiction_alias_and_boolean_formula(self) -> None:
        """复制的值沿当前版本受条件约束；比较、布尔组合和常量 false 可识别矛盾。"""
        for source in (
            "def choose(x):\n if x==1:\n  y=x\n  if y!=1:\n   return 7\n return 0\n",
            "def choose(flag):\n if flag:\n  if not flag:\n   return 7\n return 0\n",
            "def choose(x):\n if x<2 and x>5:\n  return 7\n return 0\n",
            "def choose(x):\n if False:\n  return 7\n return 0\n",
        ):
            with self.subTest(source=source):
                _, proof = self.proof(source)
                self.assertEqual("infeasible_under_model", proof.status, proof)
                self.assertTrue(proof.unsat_condition_node_ids)

    def test_reassignment_creates_new_version_and_conditions_are_not_global(self) -> None:
        """重新赋值后旧谓词不能约束新值；OR 不能被当成同时满足的 AND。"""
        for source in (
            "def choose(x):\n if x==1:\n  x=2\n  if x==2:\n   return 7\n return 0\n",
            "def choose(x):\n if x==1 or x==2:\n  return 7\n return 0\n",
            "def choose(x):\n if x=='a' or x>0:\n  if x=='a':\n   return 7\n return 0\n",
            "def choose(x):\n x,y=1,2\n if x!=y:\n  return 7\n return 0\n",
        ):
            with self.subTest(source=source):
                _, proof = self.proof(source)
                self.assertEqual("satisfiable_under_model", proof.status, proof)

    def test_specific_value_route_and_overwrite_liveness(self) -> None:
        """仅 Sink 可达不代表该次赋值可达；Def-Use 还必须跨路线存活。"""
        function, _ = self.proof("def choose(flag,value):\n if flag:\n  x=value\n else:\n  x=0\n if not flag:\n  return 7\n return 0\n")
        source = next(item for item in function.nodes.values() if item.code == "x=value")
        target = next(item for item in function.nodes.values() if item.code == "return 7")
        proof = PathConditionQuery(timeout_ms=2000).prove(function, [source.node_id, target.node_id])
        self.assertEqual("infeasible_under_model", proof.status, proof)
        function, _ = self.proof("def choose(value):\n x=value\n x=0\n return 7\n")
        source = next(item for item in function.nodes.values() if item.code == "x=value")
        target = next(item for item in function.nodes.values() if item.code == "return 7")
        edge = ProgramGraphEdge(edge_id="stale", kind="value_flow", source=source.node_id, target=target.node_id,
                                source_variable="x", read_variable="x", target_variable="x")
        proof = PathConditionQuery(timeout_ms=2000).prove(function, [source.node_id, target.node_id], value_edges=[edge])
        self.assertEqual("infeasible_under_model", proof.status, proof)

    def test_unknown_calls_loops_and_budgets_are_not_safety_proofs(self) -> None:
        """未知调用/属性和循环预算必须披露 unknown；不能用同名条件假装净化。"""
        for source in (
            "def choose(x):\n if validate(x):\n  return 7\n return 0\n",
            "def choose(x):\n if x.safe:\n  return 7\n return 0\n",
        ):
            _, proof = self.proof(source)
            self.assertEqual("unknown", proof.status, proof)
        _, proof = self.proof("def choose(x):\n while x:\n  x=1\n return 7\n", max_states=1)
        self.assertEqual("unknown", proof.status)
        self.assertTrue(proof.truncated)
        self.assertIn("path_condition_budget_exceeded", proof.reasons)

    def test_legacy_artifacts_and_unknown_ir_remain_loadable(self) -> None:
        """旧产物不补造证明，新条件契约稳定 JSON 往返。"""
        artifact = ProgramGraphArtifact.model_validate({"schema_version": "1.17"})
        self.assertFalse(artifact.functions)
        value = ConditionExpression(kind="not", operands=(ConditionExpression(kind="name", name="flag"),))
        self.assertEqual(value, ConditionExpression.model_validate_json(value.model_dump_json()))

    def test_guard_implication_uses_actual_value_and_position(self) -> None:
        """只有约束危险点实际值的先行分支可支持谓词；清洗其他值和不可达空真不能蒙混。"""
        required = ConditionExpression(kind="compare", operator="ge", operands=(
            ConditionExpression(kind="name", name="x"), ConditionExpression(kind="literal", value=0)))
        for source, expected in (
            ("def choose(x):\n if x<0:\n  return 0\n return 7\n", "implied_under_model"),
            ("def choose(x,y):\n if y<0:\n  return 0\n return 7\n", "counterexample_under_model"),
            ("def choose(x):\n if x<0:\n  return 7\n return 0\n", "counterexample_under_model"),
            ("def choose(x):\n return 0\n return 7\n", "unknown"),
        ):
            with self.subTest(source=source):
                function, _ = self.proof(source)
                target = next(item for item in function.nodes.values() if item.code == "return 7")
                result = PathConditionQuery(timeout_ms=2000).implies(function, target.node_id, required)
                self.assertEqual(expected, result.status, result)

    def test_security_proof_is_persisted_and_delivered_without_overclaim(self) -> None:
        """矛盾值流保留待核验候选；证明与未知边界按 ID 交付，不让 Guard 名称杀死风险。"""
        pack = fixtures.InstanceFieldTests.analyze(self, {"app.py": "from fastapi import FastAPI\nimport os\napp=FastAPI()\n@app.get('/')\ndef route(value:str):\n if value=='a':\n  command=value\n  if value!='a':\n   os.system(command)\n"})
        flows = list(pack.dataflows.values())
        self.assertTrue(flows, pack.flow_diagnostics)
        self.assertTrue(any(proof.status == "infeasible_under_model" for flow in flows for proof in flow.path_conditions), flows)
        restored = type(pack).model_validate_json(pack.model_dump_json())
        self.assertEqual(pack.dataflows, restored.dataflows)
        candidate = next(item for item in restored.candidates if item.dataflow_id)
        catalog = SecurityEvidenceCatalog(restored.model_dump())
        detail = catalog.detail(candidate.candidate_id, section="conditions", limit=1)
        self.assertEqual("summary", detail["items"][0]["kind"])
        self.assertTrue(detail["has_more"])
        self.assertEqual("infeasible_under_model", detail["items"][0]["status"])


if __name__ == "__main__":
    unittest.main()
