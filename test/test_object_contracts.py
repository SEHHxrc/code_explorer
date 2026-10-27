"""对象公共接口的值身份、方向、持久化和证据兼容测试；不运行目标或访问模型。"""

import hashlib
import unittest

from pydantic import ValidationError

from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphNode
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.security_analysis.flow_analysis.object_contracts import (
    LocalInstance, ObjectBinding, ObjectReferenceResolver,
)
from backend.app.services.security_analysis.flow_analysis.object_references import ObjectReferenceIndex
from backend.app.services.security_analysis.flow_analysis.program_graph import ProgramGraphSecurityFlowAnalyzer
from backend.app.services.security_analysis.flow_analysis.call_graph import ProgramArgumentBinder
from backend.app.services.security_analysis.flow_analysis.diagnostics import FlowDiagnostics
from backend.app.services.security_analysis.flow_analysis.object_effects import ObjectCallEffects
from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact
from backend.app.services.value_binding.identities import value_identity
from backend.app.services.value_binding.objects import ObjectFieldSlot, instance_field_name
from test import test_value_identities as fixtures


class ObjectContractTests(unittest.TestCase):
    """公共字段槽不依赖语言，查询协议与具体 CFG 实现隔离，旧身份仍只读兼容。"""

    def test_structured_slot_keeps_existing_identity_and_type_isolation(self) -> None:
        """类型槽与原格式一致，不把不同分配、字段或类型合并；展示根不进入身份。"""
        plain = ObjectFieldSlot(allocation_id="allocation:a", field_name="command")
        typed = plain.model_copy(update={"owner_symbol": "Worker"})
        key = hashlib.sha256(b"Worker").hexdigest()[:16]
        self.assertEqual("object:allocation:a.command", plain.value_id)
        self.assertEqual('object:allocation:a.command["@instance:' + key + '"]', typed.value_id)
        self.assertEqual("a.command", instance_field_name("a", "Worker", "command").split("[", 1)[0])
        for other in (plain.model_copy(update={"allocation_id": "allocation:b"}),
                      plain.model_copy(update={"field_name": "safe"}),
                      typed.model_copy(update={"owner_symbol": "Other"})):
            self.assertNotEqual(typed.value_id, other.value_id)
        self.assertEqual(typed, ObjectFieldSlot.model_validate_json(typed.model_dump_json()))
        with self.assertRaises(ValidationError):
            typed.field_name = "changed"

    def test_slot_rejects_paths_and_unknown_allocation_instead_of_guessing(self) -> None:
        """只有直属标识符与非空分配可进入精确字段接口，复杂/动态路径不降格冒充直属字段。"""
        for field in ("", "command.child", 'command["key"]', "command()"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ObjectFieldSlot(allocation_id="allocation:a", field_name=field)
        with self.assertRaises(ValidationError):
            ObjectFieldSlot(allocation_id="", field_name="command")
        with self.assertRaises(ValidationError):
            ObjectFieldSlot(allocation_id="allocation:a", field_name="command", owner_symbol="")

    def test_unknown_ambiguous_and_unique_references_are_distinct(self) -> None:
        """空集和含未知的集合均不可强更新；只有唯一非空分配可用于字段共享。"""
        for candidates, expected in ((frozenset(), None), (frozenset({""}), None),
                                     (frozenset({"a", ""}), None), (frozenset({"a", "b"}), None),
                                     (frozenset({"a"}), "a")):
            with self.subTest(candidates=candidates):
                self.assertEqual(expected, ObjectBinding(candidates).unique_allocation)

    def test_read_and_write_slots_roundtrip_without_duplicate_identity_maps(self) -> None:
        """同一操作的读写身份分开；新槽优先，旧 1.9 值身份和更早产物继续回退读取。"""
        read = ObjectFieldSlot(allocation_id="allocation:old", field_name="command")
        write = read.model_copy(update={"allocation_id": "allocation:new"})
        node = ProgramGraphNode(node_id="n", method_id="f", language="test", kind="assignment",
            definition_object_slots={"item.command": write}, use_object_slots={"item.command": read})
        restored = ProgramGraphNode.model_validate_json(node.model_dump_json())
        self.assertEqual(read.value_id, value_identity(restored, "item.command", write=False))
        self.assertEqual(write.value_id, value_identity(restored, "item.command", write=True))
        self.assertFalse(restored.definition_value_ids)
        self.assertFalse(restored.use_value_ids)
        legacy = ProgramGraphNode.model_validate({"node_id": "n", "method_id": "f", "language": "test",
            "kind": "call", "use_value_ids": {"item.command": read.value_id}})
        self.assertEqual(read.value_id, value_identity(legacy, "item.command", write=False))
        self.assertEqual("lexical:other", value_identity(legacy, "other", write=False))
        for version in ("1.0", "1.8", "1.9", "1.10"):
            self.assertEqual(version, ProgramGraphArtifact.model_validate({"schema_version": version}).schema_version)

    def test_current_resolver_supplies_structured_fields_through_common_interface(self) -> None:
        """真实 CFG 实现消费公共契约；未知字段返回 None，生成 Overlay 不修改生产者图。"""
        graph = fixtures.ValueIdentityTests().graph(inner_reads_outer=True).model_copy(update={"object_assignment": "reference"})
        instance = LocalInstance("outer", "call", "allocation:test", "Worker", "value", ("command",))
        resolver: ObjectReferenceResolver = ObjectReferenceIndex(graph, [instance])
        node = graph.nodes["inner_sink"]
        slot = resolver.field_binding(node, "value.command", write=False)
        self.assertEqual(ObjectFieldSlot(allocation_id="allocation:test", field_name="command"), slot)
        typed_name = instance_field_name("value", "Worker", "command")
        self.assertEqual("Worker", resolver.field_binding(node, typed_name, write=False).owner_symbol)
        self.assertIsNone(resolver.field_binding(node, "value.unknown", write=False))
        self.assertIsNone(resolver.field_binding(node, "value.command.nested", write=False))
        nodes = {**graph.nodes, node.node_id: node.model_copy(update={"uses": ["value.command"]})}
        supplied = graph.model_copy(update={"nodes": nodes})
        before = supplied.model_dump_json()
        overlay = resolver.bind_fields(supplied)
        self.assertEqual(before, supplied.model_dump_json())
        self.assertEqual(slot, overlay.nodes[node.node_id].use_object_slots["value.command"])
        self.assertFalse(overlay.nodes[node.node_id].use_value_ids)

    def test_evidence_uses_structured_allocation_without_string_splitting(self) -> None:
        """新分配 ID 按不透明字符串保留；历史 1.9 字段来源仍能生成相同别名步骤。"""
        graph = fixtures.ValueIdentityTests().graph()
        slot = ObjectFieldSlot(allocation_id="allocation:opaque.with.dots", field_name="command")
        origin = FieldValueOrigin(symbol="f", location=graph.location, expression="alias=original")
        node = graph.nodes["inner_sink"].model_copy(update={"uses": ["alias.command"],
            "use_object_slots": {"alias.command": slot}, "object_alias_origins": {"alias.command": [origin]}})
        graph = graph.model_copy(update={"nodes": {**graph.nodes, node.node_id: node}})
        analyzer = ProgramGraphSecurityFlowAnalyzer()
        steps = analyzer._field_alias_steps(graph, node.node_id, {"alias.command"})
        self.assertEqual([slot.allocation_id], [step.object_allocation_id for step in steps])
        legacy_node = node.model_copy(update={"use_object_slots": {},
            "use_value_ids": {"alias.command": "object:allocation:legacy.command"}})
        graph = graph.model_copy(update={"nodes": {**graph.nodes, node.node_id: legacy_node}})
        steps = analyzer._field_alias_steps(graph, node.node_id, {"alias.command"})
        self.assertEqual(["allocation:legacy"], [step.object_allocation_id for step in steps])

    def test_object_effect_budget_is_not_reported_as_a_safe_or_partial_summary(self) -> None:
        """字段接口超预算必须整项拒绝并按预算类记前沿，不保留部分精确写回。"""
        graph = fixtures.ValueIdentityTests().graph().model_copy(update={"object_assignment": "reference"})
        node = graph.nodes["inner"].model_copy(update={"uses": ["value.command"]})
        graph = graph.model_copy(update={"nodes": {**graph.nodes, node.node_id: node}})
        diagnostics = FlowDiagnostics()
        query = ObjectCallEffects(ProgramGraphArtifact(functions={graph.method_id: graph}),
            SemanticIndex(SemanticIndexArtifact()), ProgramArgumentBinder(), diagnostics, max_fields=0)
        self.assertIsNone(query.summary(graph, "value"))
        self.assertEqual(1, diagnostics.counts["object_call_effect_budget_exceeded"])
        self.assertEqual("budget_limit", diagnostics.items[0]["diagnostic_kind"])


if __name__ == "__main__":
    unittest.main()
