"""公共调用后置 CFG 与值边界定位契约，不包含语言或安全规则。"""

import unittest

from backend.app.services.program_graph import ProgramCallSite, ProgramGraphArtifact, ProgramGraphEdge, ProgramGraphNode
from backend.app.services.program_graph.contracts import InstanceMethodContext
from backend.app.services.security_analysis.flow_analysis.call_graph import ProgramArgumentBinder
from backend.app.services.security_analysis.flow_analysis.post_calls import append_post_operations, normal_exit_values
from backend.app.services.security_analysis.flow_analysis.value_boundaries import ValueBoundaryIndex
from backend.app.services.security_analysis.ir import IRLocation, IRValueBoundary
from test import test_value_identities as fixtures


class PostCallValueTests(unittest.TestCase):
    """正常后置定义不得移到调用前或异常边上，同一源码范围必须按精确身份区分。"""

    def test_reference_arguments_are_bound_per_position_not_by_variable_occurrence(self) -> None:
        """同一变量可在一处直接传递、另一处参与表达式；普通值绑定保留，对象绑定只认前者。"""
        graph = fixtures.ValueIdentityTests().graph().model_copy(update={"parameters": ["first", "second"]})
        call = ProgramCallSite(callsite_id="call", name="change", location=graph.location,
            positional_arguments=[["value"], ["value"]], positional_references=["value", ""])
        binder = ProgramArgumentBinder()
        self.assertEqual([("first", ("value",)), ("second", ("value",))], binder.bind(call, graph))
        self.assertEqual([("first", "value")], binder.bind_references(call, graph))
        self.assertFalse(binder.bind_references(call.model_copy(update={"positional_references": []}), graph))
        keyword = call.model_copy(update={"positional_arguments": [], "keyword_arguments": {"first": ["value"], "second": ["value"]},
            "keyword_references": {"first": "value", "second": ""}})
        self.assertEqual([("first", "value")], binder.bind_references(keyword, graph))
        method = graph.model_copy(update={"instance_context": InstanceMethodContext(owner_symbol="Worker", receiver="this")})
        excluded = call.model_copy(update={"receiver": "worker", "receiver_field_bindings": {}})
        self.assertFalse(binder.bind_references(excluded, method))

    def test_normal_and_exception_edges_have_different_writeback_order(self) -> None:
        """正常边经过有序后置操作，异常边保留原目标，生产者图不修改。"""
        graph = fixtures.ValueIdentityTests().graph()
        exception = ProgramGraphEdge(edge_id="exception", source="inner", target="outer_sink", kind="cfg", branch="exception")
        graph = graph.model_copy(update={"edges": [*graph.edges, exception]})
        post = [ProgramGraphNode(node_id=name, method_id="f", language="test", kind="operation") for name in ("post1", "post2")]
        before = graph.model_dump_json()
        view = append_post_operations(graph, {"inner": post})
        edges = {(edge.source, edge.target, edge.branch) for edge in view.edges}
        self.assertIn(("inner", "post1", "normal"), edges)
        self.assertIn(("post1", "post2", "normal"), edges)
        self.assertIn(("post2", "inner_sink", None), edges)
        self.assertIn(("inner", "outer_sink", "exception"), edges)
        self.assertEqual(before, graph.model_dump_json())
        self.assertTrue(all(edge.kind == "cfg" for edge in view.edges))

    def test_normal_exit_observation_is_reused_and_excludes_exception(self) -> None:
        """多个消费者读取同一正常出口节点，异常边不穿过值观察点，原图保持不变。"""
        graph = fixtures.ValueIdentityTests().graph()
        exception = ProgramGraphEdge(edge_id="exception", source="inner", target=graph.exit_node_id, kind="cfg", branch="exception")
        graph = graph.model_copy(update={"edges": [*graph.edges, exception]})
        before = graph.model_dump_json()
        first, identity = normal_exit_values(graph, {"value"})
        second, other = normal_exit_values(first, {"other"})
        self.assertEqual(identity, other)
        self.assertEqual({"value", "other"}, set(second.nodes[identity].uses))
        self.assertEqual(len(first.edges), len(second.edges))
        self.assertTrue(any(edge.source == "inner" and edge.target == graph.exit_node_id and edge.branch == "exception" for edge in second.edges))
        self.assertEqual(before, graph.model_dump_json())

    def test_missing_or_conflicting_post_nodes_do_not_create_a_new_path(self) -> None:
        """无正常出口不新建可达路径；已有节点 ID 或重复 ID 不能覆盖原图。"""
        graph = fixtures.ValueIdentityTests().graph()
        post = ProgramGraphNode(node_id="post", method_id="f", language="test", kind="operation")
        self.assertNotIn("post", append_post_operations(graph, {graph.exit_node_id: [post]}).nodes)
        with self.assertRaises(ValueError):
            append_post_operations(graph, {"inner": [graph.nodes["outer"]]})
        with self.assertRaises(ValueError):
            append_post_operations(graph, {"inner": [post, post]})

    def test_explicit_boundary_node_ids_never_fall_back_to_same_line(self) -> None:
        """显式节点身份不存在便拒绝，不能借同一行的其他定义续接值流。"""
        graph = fixtures.ValueIdentityTests().graph()
        scope = IRLocation(**graph.location.model_dump())
        boundary = IRValueBoundary(boundary_id="b", kind="test", source_scope=scope, source_location=scope,
            source_names=("value",), target_scope=scope, target_location=scope, target_names=("value",),
            source_node_id="outer", target_node_id="inner")
        artifact = ProgramGraphArtifact(functions={graph.method_id: graph})
        self.assertEqual(1, len(ValueBoundaryIndex(artifact, [boundary]).for_source(graph.method_id)))
        for field in ("source_node_id", "target_node_id"):
            invalid = IRValueBoundary(**{**boundary.__dict__, field: "missing"})
            self.assertFalse(ValueBoundaryIndex(artifact, [invalid]).for_source(graph.method_id))


if __name__ == "__main__":
    unittest.main()
