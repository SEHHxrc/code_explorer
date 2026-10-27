"""声明级公共类型事实回归；不根据名字或最近行号猜测使用点绑定。"""

from pathlib import Path
import tempfile
import unittest

from backend.app.services.dependency_analyzer.compiler import CompilerBindingIndex
from backend.app.services.dependency_analyzer.models import Definition
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.semantic_index import (
    ProgramGraphSemanticProvider, SemanticIndex, SemanticIndexArtifact,
    SemanticLocation, SemanticTypeView, VariableTypeFact,
)
from backend.app.services.semantic_index.resolution import CompilerTypeBinding, DeclarationBinding, ProjectSemanticResult


class SemanticTypeTests(unittest.TestCase):
    """所有编译器生产者复用同一事实装配；有歧义时保留细节而非最后写入获胜。"""

    owner = "src/app.ts::run"

    def binding(self, line: int, literal: str, *, role: str = "variable", column: int = 8) -> CompilerTypeBinding:
        """构造已有编译器协议事实；坐标指变量声明，不是变量使用位置。"""
        return CompilerTypeBinding(
            name="value", type_literal=literal, role=role,
            location=SemanticLocation(path="src/app.ts", line=line, column=column, end_line=line, end_column=column + 5),
            owner=DeclarationBinding(name="run", kind="function", has_body=True,
                                     location=SemanticLocation(path="src/app.ts", line=1, end_line=6)), owner_scope="callable",
        )

    def compiler(self, bindings: list[CompilerTypeBinding]) -> CompilerBindingIndex:
        """使用公共映射器，不安装或启动语言工具。"""
        definition = Definition(fqn=self.owner, file="src/app.ts", name="run", kind="function", lang="typescript", line=1, end_line=6)
        return CompilerBindingIndex(ProjectSemanticResult(backend="typescript", status="completed", types=bindings),
                                    {self.owner: definition})

    def test_shadowed_declarations_survive_even_with_the_same_type(self) -> None:
        for inner_type in ("string", "number"):
            with self.subTest(inner_type=inner_type):
                bindings = [self.binding(1, "string", role="parameter"), self.binding(3, inner_type)]
                artifact = self.compiler(bindings).enrich(SemanticIndexArtifact())
                index = SemanticIndex(artifact)
                self.assertIsInstance(index, SemanticTypeView)
                self.assertEqual("1.2", artifact.schema_version)
                self.assertEqual(2, len(index.variable_types_for(self.owner, "value")))
                self.assertIsNone(index.variable_type(self.owner, "value"))
                self.assertEqual("string", index.variable_types_for(self.owner, "value", role="parameter")[0].type_literal)
                self.assertEqual(inner_type, index.variable_types_at(bindings[1].location)[0].type_literal)
                self.assertEqual(2, len({item.declaration_id for item in artifact.variable_types.values()}))
                self.assertEqual(1, artifact.backend_runs[0]["ambiguous_type_slots"])
                self.assertEqual(2, artifact.coverage.variable_type_count)

    def test_conflicting_types_for_one_declaration_are_not_overwritten(self) -> None:
        bindings = [self.binding(1, "string", role="parameter"), self.binding(1, "number", role="parameter")]
        artifact = self.compiler(bindings).enrich(SemanticIndexArtifact())
        index = SemanticIndex(artifact)
        matches = index.variable_types_at(bindings[0].location, owner_symbol=self.owner, name="value")
        self.assertEqual({"string", "number"}, {item.type_literal for item in matches})
        self.assertEqual(1, len({item.declaration_id for item in matches}))
        self.assertIsNone(index.variable_type(self.owner, "value"))

    def test_duplicates_reordering_and_repeated_enrichment_are_stable(self) -> None:
        bindings = [self.binding(1, "string", role="parameter"), self.binding(3, "number")]
        compiler = self.compiler([*bindings, bindings[0]])
        initial = compiler.enrich(SemanticIndexArtifact())
        self.assertEqual(initial, compiler.enrich(initial))
        self.assertEqual(initial, self.compiler(list(reversed([*bindings, bindings[0]]))).enrich(SemanticIndexArtifact()))
        self.assertEqual(initial, SemanticIndexArtifact.model_validate_json(initial.model_dump_json()))

    def test_legacy_type_slots_remain_readable_but_missing_coordinates_do_not_create_identity(self) -> None:
        legacy = VariableTypeFact(fact_id="old", owner_symbol=self.owner, name="value", language="typescript", type_literal="string")
        artifact = SemanticIndexArtifact(schema_version="1.0", variable_types={SemanticIndex.variable_type_key(self.owner, "value"): legacy})
        self.assertEqual(legacy, SemanticIndex(artifact).variable_type(self.owner, "value"))
        self.assertEqual("unknown", legacy.role)
        binding = self.binding(1, "number").model_copy(update={"location": SemanticLocation(path="src/app.ts")})
        enriched = self.compiler([binding]).enrich(artifact)
        self.assertEqual(1, len(enriched.variable_types))
        fact = next(iter(enriched.variable_types.values()))
        self.assertEqual("", fact.declaration_id)
        self.assertEqual((), SemanticIndex(enriched).variable_types_at(binding.location))
        self.assertEqual("1.0", artifact.schema_version)
        self.assertEqual({SemanticIndex.variable_type_key(self.owner, "value"): legacy}, artifact.variable_types)

    def test_position_query_does_not_resolve_use_sites_or_ignore_conflicting_coordinates(self) -> None:
        bindings = [self.binding(1, "string", role="parameter"), self.binding(3, "number")]
        index = SemanticIndex(self.compiler(bindings).enrich(SemanticIndexArtifact()))
        self.assertEqual((), index.variable_types_at(SemanticLocation(path="src/app.ts", line=4)))
        self.assertEqual((), index.variable_types_at(bindings[0].location, owner_symbol="other"))
        self.assertEqual((), index.variable_types_at(bindings[0].location, name="other"))
        fact = index.variable_types_at(bindings[0].location)[0]
        self.assertEqual((fact,), index.variable_types_at(fact.location))
        self.assertEqual((), index.variable_types_at(fact.location.model_copy(update={"location_id": "other"})))

    def test_parameter_projection_uses_declared_parameter_not_inner_shadow(self) -> None:
        source = "function run(value: string) {\n {\n  const value = 42;\n }\n return value;\n}\n"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "src").mkdir()
            Path(directory, "src/app.ts").write_text(source, encoding="utf8")
            graph = ProgramGraphService().analyze(directory)
        bindings = [self.binding(1, "string", role="parameter"), self.binding(3, "number")]
        enriched = ProgramGraphSemanticProvider().enrich(self.compiler(bindings).enrich(SemanticIndexArtifact()), program_graph=graph)
        self.assertEqual("1.2", enriched.schema_version)
        self.assertEqual(["string"], [item.type_literal for item in enriched.parameters.values() if item.name == "value"])
        conflict = self.compiler([bindings[0], self.binding(1, "number", role="parameter")]).enrich(SemanticIndexArtifact())
        projected = ProgramGraphSemanticProvider().enrich(conflict, program_graph=graph)
        self.assertEqual([""], [item.type_literal for item in projected.parameters.values() if item.name == "value"])


if __name__ == "__main__":
    unittest.main()
