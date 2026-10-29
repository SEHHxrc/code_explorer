"""将语言工具声明绑定映射到已有符号；不另建调用图或传播求解器。"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import replace
import hashlib

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.semantic_index import SemanticIndexArtifact, VariableTypeFact
from backend.app.services.semantic_index.resolution import DeclarationBinding, DeclarationSymbolIndex, ProjectSemanticResult
from backend.app.services.semantic_index.references import project_variable_references

from .models import Definition, Reference, ReferenceResolution


class CompilerBindingIndex:
    """运行内调用位置和声明索引；成熟绑定成功时不执行旧同名目标推测。"""

    def __init__(self, result: ProjectSemanticResult | list[ProjectSemanticResult], definitions: Mapping[str, Definition]) -> None:
        """输入一次项目语义结果和既有符号，建立共享、确定性查询索引。"""
        self.results = result if isinstance(result, list) else [result]
        self.backends = {item.backend: item for item in self.results}
        self.definitions = definitions
        self.declarations = DeclarationSymbolIndex(
            (item.fqn, (item.file, item.name, item.kind, item.line, item.end_line))
            for item in definitions.values()
        )
        self.bindings = {
            (run.backend, ProgramIdentity.callsite_id(item.location.path, item.location.line, item.location.column,
                                        item.location.end_line, item.location.end_column)): item
            for run in self.results for item in run.calls
        }
        self.counts: dict[str, Counter[str]] = defaultdict(Counter)

    def resolutions(self, ref: Reference) -> tuple[list[ReferenceResolution], str | None]:
        """返回已映射目标或显式回退原因；不把签名当作唯一运行时实现。"""
        backend = "typescript" if ref.lang in {"javascript", "typescript"} else ref.lang
        result = self.backends.get(backend)
        if result is None or ref.kind not in {"call", "new"}:
            return [], None
        counts = self.counts[result.backend]
        if result.status in {"unavailable", "failed"}:
            reason = str(result.diagnostics[0]["reason"]) if result.diagnostics else "compiler_unavailable"
            counts["fallback_calls"] += 1
            return [], reason
        key = ProgramIdentity.callsite_id(ref.file, ref.line, ref.column, ref.end_line, ref.end_column)
        binding = self.bindings.get((backend, key))
        if binding is None:
            counts["fallback_calls"] += 1
            return [], "compiler_callsite_not_mapped"
        targets: set[str] = set()
        declarations = binding.declarations
        if ref.kind == "new":
            classes = [item for item in declarations if item.kind == "class"]
            declarations = classes or declarations  # 继承来的构造签名不能把 new Child 误标为 new Base。
        for declaration in declarations:
            if not declaration.has_body:
                continue
            symbol = self.declarations.symbol_for(declaration)
            if symbol is None:
                continue
            definition = self.definitions[symbol]
            if ref.kind == "new" and definition.kind == "constructor":
                symbol = definition.parent_fqn  # 分配目标统一是类，构造参数由已有对象层绑定。
            if symbol in self.definitions:
                targets.add(symbol)
        if not targets or binding.unresolved_reason:
            counts["fallback_calls"] += 1
            return [], binding.unresolved_reason or "compiler_declaration_not_mapped"
        counts["bound_calls"] += 1
        return [ReferenceResolution(
            target=target, dispatch=binding.dispatch, target_scope="project",
            resolution_method=f"{backend}_declaration_binding", target_certainty="may",
            confidence="high",
        ) for target in sorted(targets)], None

    @staticmethod
    def fallback(resolution: ReferenceResolution, reason: str | None) -> ReferenceResolution:
        """保留旧结果，但语言工具未确认的项目目标一律为 may，并注明回退原因。"""
        if not reason:
            return resolution
        return replace(resolution, fallback_reason=reason,
                       target_certainty="may" if resolution.target else "unresolved")

    def enrich(self, artifact: SemanticIndexArtifact) -> SemanticIndexArtifact:
        """追加编译器类型、返回类型与运行来源；不更改已统一生成的调用目标。"""
        types = dict(artifact.variable_types)
        callables = dict(artifact.callables)
        existing_slots: dict[tuple[str, str], list[str]] = defaultdict(list)
        for key, fact in types.items():
            existing_slots[(fact.owner_symbol, fact.name)].append(key)
        replaced_slots: set[tuple[str, str, str]] = set()
        native_slots: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for backend, item in ((run.backend, item) for run in self.results for item in run.types):
            type_method = f"{backend}_type_checker"
            owner = self.declarations.symbol_for(item.owner) if item.owner else (
                ProgramIdentity.file_id(item.location.path) if item.owner_scope == "module" else None)
            if not owner:
                continue
            if item.role == "return":
                if owner in callables:
                    callables[owner] = callables[owner].model_copy(update={"return_type": item.type_literal})
                continue
            if not item.name or not item.type_literal:
                continue
            slot = (owner, item.name)
            producer_slot = (backend, *slot)
            if producer_slot not in replaced_slots:
                for key in existing_slots.get(slot, []):
                    existing = types.get(key)
                    if existing is not None and existing.resolution_method in {type_method, "syntax_or_heuristic"}:
                        types.pop(key)
                replaced_slots.add(producer_slot)
            native_slots[backend].add(slot)
            location = item.location
            location_id = ProgramIdentity.location_id(location.path, location.line, location.column,
                                                      location.end_line, location.end_column)
            location = location.model_copy(update={"path": ProgramIdentity.file_id(location.path), "location_id": location_id})
            identity = "\x1f".join((owner, item.name, item.role, location_id))
            declaration_id = ProgramIdentity.declaration_id(owner, item.name, item.role, location.path, location.line,
                                                           location.column, location.end_line, location.end_column)
            fact_id = "vartype:" + hashlib.sha256("\x1f".join((identity, type_method, item.type_literal)).encode("utf-8")).hexdigest()[:24]
            types[fact_id] = VariableTypeFact(
                fact_id=fact_id, declaration_id=declaration_id, role=item.role,
                owner_symbol=owner, name=item.name,
                language="go" if backend == "go" else ("typescript" if item.location.path.endswith((".ts", ".tsx", ".mts", ".cts")) else "javascript"),
                type_literal=item.type_literal, provenance="inferred", confidence="high",
                resolution_method=type_method, location=location,
            )
        slot_counts = Counter((fact.owner_symbol, fact.name) for fact in types.values())
        for backend, slots in native_slots.items():
            self.counts[backend]["ambiguous_type_slots"] = sum(slot_counts[slot] > 1 for slot in slots)
        native_ssa = dict(artifact.native_ssa)
        for result in self.results:
            mapped = 0
            for function in result.ssa_functions:
                symbol = self.declarations.symbol_for(DeclarationBinding(
                    location=function.location, name=function.name, kind=function.kind, has_body=True))
                if symbol is not None:
                    native_ssa[symbol] = function
                    mapped += 1
            if result.ssa_functions:
                self.counts[result.backend]["ssa_mapped_functions"] = mapped
                self.counts[result.backend]["ssa_unmapped_functions"] = len(result.ssa_functions) - mapped
        enriched = artifact.model_copy(update={
            "schema_version": "1.2" if native_slots and artifact.schema_version not in {"1.3", "1.4", "1.5"} else artifact.schema_version,
            "variable_types": types, "callables": callables, "native_ssa": native_ssa,
            "backend_runs": [result.summary() | dict(self.counts[result.backend]) for result in self.results],
            "coverage": artifact.coverage.model_copy(update={"variable_type_count": len(types)}),
            "limitations": list(dict.fromkeys([*artifact.limitations,
                "编译器结果仅提供声明绑定和类型信息，不证明唯一动态目标、完整堆传播或路径可行性。",
                "Go 原生 SSA 保留 CFG/φ/操作数关系；内存读写不等于别名分析，现有安全传播仍使用公共 ProgramGraph。",
                "JS/TS 未显式配置 moduleDetection 时使用独立模块语义，传统共享全局脚本可能回退。"])),
        })
        return project_variable_references(enriched, self.results, self.declarations)
