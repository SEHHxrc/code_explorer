"""使用点→源码声明的公共绑定契约，不等于运行时对象、到达定义或污点边。"""

import unittest

from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact, SemanticLocation, SemanticReferenceView
from backend.app.services.semantic_index.resolution import (
    CompilerVariableDeclaration, CompilerVariableReference, DeclarationBinding,
)
from test import test_semantic_types as type_fixtures


class SemanticReferenceTests(unittest.TestCase):
    """直接消费编译器事实，不执行源码或按名字/行号重新求解。"""

    def declaration(self, line: int, *, role: str = "variable") -> CompilerVariableDeclaration:
        """复用声明类型夹具的位置，验证两类事实使用同一声明身份。"""
        binding = type_fixtures.SemanticTypeTests().binding(line, "string", role=role)
        return CompilerVariableDeclaration(location=binding.location, name=binding.name, role=role,
                                           owner=binding.owner, owner_scope="callable")

    def reference(self, declarations: list[CompilerVariableDeclaration], *, line: int = 5, column: int = 9) -> CompilerVariableReference:
        """声明候选由生产者明确提供，不因引用名相同而补造目标。"""
        return CompilerVariableReference(name="value", location=SemanticLocation(path="src/app.ts", line=line, column=column,
            end_line=line, end_column=column + 5), declarations=declarations, owner=self.declaration(1).owner, owner_scope="callable")

    def analyze(self, references: list[CompilerVariableReference], declarations: list[CompilerVariableDeclaration] | None = None,
                *, with_type: bool = False) -> SemanticIndexArtifact:
        """通过生产 CompilerBindingIndex 投影；不启动语言工作进程。"""
        fixture = type_fixtures.SemanticTypeTests()
        compiler = fixture.compiler([fixture.binding(1, "string", role="parameter")] if with_type else [])
        compiler.results[0].variable_references = references
        compiler.results[0].variable_declarations = declarations or []
        return compiler.enrich(SemanticIndexArtifact())

    def test_reference_keeps_exact_declaration_and_shared_type_identity(self) -> None:
        outer, inner = self.declaration(1, role="parameter"), self.declaration(3)
        artifact = self.analyze([self.reference([outer]), self.reference([inner], column=25)], [outer, inner], with_type=True)
        index = SemanticIndex(artifact)
        self.assertIsInstance(index, SemanticReferenceView)
        self.assertEqual("1.3", artifact.schema_version)
        references = index.variable_references_at(SemanticLocation(path="./src\\app.ts", line=5))
        self.assertEqual(2, len(references))
        first = references[0]
        self.assertEqual(first, index.variable_reference(first.reference_id))
        target = index.variable_declaration(first.declaration_ids[0])
        self.assertEqual(1, target.location.line)
        self.assertEqual((first,), index.variable_references_to(target.declaration_id))
        types = index.declaration_types_for_reference(first.reference_id)
        self.assertEqual([target.declaration_id], [item.declaration_id for item in types])
        self.assertEqual(["string"], [item.type_literal for item in types])
        self.assertEqual((), index.declaration_types_for_reference(references[1].reference_id))

    def test_unknown_type_does_not_drop_declaration_or_binding(self) -> None:
        artifact = self.analyze([self.reference([self.declaration(1)])])
        fact = next(iter(artifact.variable_references.values()))
        self.assertTrue(fact.declaration_ids)
        self.assertIsNone(fact.unresolved_reason)
        self.assertEqual({}, artifact.variable_types)
        self.assertEqual(artifact, SemanticIndexArtifact.model_validate_json(artifact.model_dump_json()))

    def test_missing_target_is_not_replaced_by_same_name_or_nearest_declaration(self) -> None:
        artifact = self.analyze([self.reference([])], [self.declaration(1), self.declaration(3)])
        fact = next(iter(artifact.variable_references.values()))
        self.assertEqual([], fact.declaration_ids)
        self.assertEqual("compiler_variable_reference_unresolved", fact.unresolved_reason)
        self.assertEqual((), SemanticIndex(artifact).declaration_types_for_reference(fact.reference_id))

    def test_unmapped_anonymous_owner_does_not_change_known_outer_binding(self) -> None:
        reference = self.reference([self.declaration(1, role="parameter")])
        reference.owner = DeclarationBinding(location=SemanticLocation(path="src/app.ts", line=4), name="", kind="function", has_body=True)
        artifact = self.analyze([reference], with_type=True)
        fact = next(iter(artifact.variable_references.values()))
        self.assertEqual("", fact.owner_symbol)
        self.assertEqual("reference_owner_not_mapped", fact.owner_unresolved_reason)
        self.assertIsNone(fact.unresolved_reason)
        self.assertEqual(1, len(SemanticIndex(artifact).declaration_types_for_reference(fact.reference_id)))

    def test_conflicting_binding_is_retained_as_unresolved_not_last_writer(self) -> None:
        first, second = self.reference([self.declaration(1)]), self.reference([self.declaration(3)])
        artifact = self.analyze([first, first, second])
        fact = next(iter(artifact.variable_references.values()))
        self.assertEqual(1, len(artifact.variable_references))
        self.assertEqual(2, len(fact.declaration_ids))
        self.assertEqual("compiler_variable_reference_conflict", fact.unresolved_reason)
        self.assertEqual((), SemanticIndex(artifact).declaration_types_for_reference(fact.reference_id))

    def test_incomplete_ranges_and_old_artifacts_never_fabricate_binding(self) -> None:
        declaration = self.declaration(1).model_copy(update={"location": SemanticLocation(path="src/app.ts")})
        artifact = self.analyze([self.reference([declaration])])
        fact = next(iter(artifact.variable_references.values()))
        self.assertEqual("variable_declaration_location_incomplete", fact.unresolved_reason)
        self.assertEqual({}, artifact.variable_declarations)
        for version in ("1.0", "1.1", "1.2"):
            index = SemanticIndex.load({"schema_version": version})
            self.assertEqual((), index.variable_references_at(SemanticLocation(path="src/app.ts", line=5)))
            self.assertIsNone(index.variable_reference("missing"))
            self.assertEqual((), index.variable_references_to("missing"))


if __name__ == "__main__":
    unittest.main()
